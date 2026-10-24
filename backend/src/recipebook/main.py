import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl, urlparse
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID
from fastapi import FastAPI, Request, Response, Depends, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from recipebook.config import Settings, ROOT
from recipebook.db.store import Store, now, uid
from recipebook.domain.recipe import Model, RecipeContent
from recipebook.llm.assistant import Assistant

class APIError(Exception):
    def __init__(self,status,code,message): self.status,self.code,self.message=status,code,message

def fail(status,code,message): raise APIError(status,code,message)
def digest(text): return hashlib.sha256(text.encode()).hexdigest()
def normalize(text): return ' '.join(text.lower().replace('ё','е').split())
def etag(row):return f'"recipe:{row["id"]}:{row["revision"]}"'
def record(row):return {'id':row['id'],'revision':row['revision'],'createdAt':row['created'],'updatedAt':row['updated'],**row['data']}

class Login(Model): initData: str = Field(max_length=20000)
class Notes(Model): userNotes: str = Field(max_length=20000)
class Conversation(Model): recipeId: str | None = None
class Send(Model):
    clientRequestId: UUID
    text: str = Field(min_length=1,max_length=8000)
    recipeContext: dict | None = None
    selectedIdea: dict | None = None
class Save(Model): targetRecipeId: str | None = None

def create_app(settings=None,assistant=None):
    s=settings or Settings()
    if s.database_url.startswith('sqlite:///./'):
        s.database_url='sqlite:///'+str(ROOT/s.database_url.removeprefix('sqlite:///./'))
    store=Store(s.database_url); model=assistant or Assistant(s)
    @asynccontextmanager
    async def lifespan(app):
        with store.transaction() as c:
            for r in store.list(c,'generation'):
                if r['data']['status']=='running':store.put(c,r,{**r['data'],'status':'interrupted','error':'Генерация прервана перезапуском'})
        yield
        store.engine.dispose()
    app=FastAPI(title='RecipeBook',lifespan=lifespan)
    app.state.store=store
    @app.exception_handler(APIError)
    async def api_error(request,exc):
        return JSONResponse(status_code=exc.status,content={'error':{'code':exc.code,'message':exc.message,'requestId':uid(),'details':None}})
    @app.exception_handler(RequestValidationError)
    async def validation(request,exc):
        return JSONResponse(status_code=422,content={'error':{'code':'validation_error','message':'Проверьте поля рецепта','details':[{'path':'.'.join(map(str,e['loc'])),'message':e['msg']} for e in exc.errors()]}})
    @app.middleware('http')
    async def origin_guard(request,call_next):
        if request.method not in ('GET','HEAD','OPTIONS') and request.url.path.startswith('/api/'):
            origin=request.headers.get('origin')
            allowed={s.public_app_url.rstrip('/')}
            if s.app_env=='development': allowed|={'http://localhost:5173','http://127.0.0.1:5173'}
            if origin not in allowed:
                return JSONResponse(status_code=403,content={'error':{'code':'forbidden','message':'Недопустимый источник запроса'}})
        return await call_next(request)
    def auth(request:Request):
        token=request.cookies.get('recipebook_session','')
        with store.transaction() as c:r=store.get(c,digest(token),'session') if token else None
        if not r or r['data']['expires']<time.time():fail(401,'unauthorized','Откройте приложение через Telegram')
        return r['data']['user']
    def fetch(c,id,kind):
        row=store.get(c,id,kind)
        if not row:fail(404,'not_found','Запись не найдена')
        return row
    def revision(request,row):
        value=request.headers.get('if-match')
        if not value:fail(428,'revision_required','Не указана версия рецепта')
        if value!=etag(row):fail(412,'revision_conflict','Рецепт изменился. Сохраните копию или откройте новую версию.')
    def issue(response,user):
        token=secrets.token_urlsafe(32)
        with store.transaction() as c:store.add(c,'session',{'user':user,'expires':time.time()+s.session_ttl_seconds},id=digest(token))
        response.set_cookie('recipebook_session',token,httponly=True,secure=s.app_env=='production',samesite='strict',max_age=s.session_ttl_seconds)
        return {'user':{'telegramId':user}}
    @app.get('/api/v1/status')
    def status():return {'devLogin':s.dev_auth_enabled and s.app_env=='development','model':s.llm_provider if s.llm_provider=='fake' or (s.llm_api_key and s.llm_model) else 'disabled'}
    @app.post('/api/v1/auth/dev')
    def dev(request:Request,response:Response):
        if not s.dev_auth_enabled or s.app_env!='development' or request.client.host not in ('127.0.0.1','::1','testclient'):fail(403,'forbidden','Локальный вход отключён')
        return issue(response,0)
    @app.post('/api/v1/auth/telegram')
    def login(data:Login,response:Response):
        try:
            pairs=parse_qsl(data.initData,strict_parsing=True)
            fields=dict(pairs)
            if len(fields)!=len(pairs):raise ValueError()
            supplied=fields.pop('hash')
            secret=hmac.new(b'WebAppData',s.telegram_bot_token.encode(),hashlib.sha256).digest()
            check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
            valid=hmac.compare_digest(supplied,hmac.new(secret,check.encode(),hashlib.sha256).hexdigest())
            age=time.time()-int(fields['auth_date']); user=json.loads(fields['user'])['id']
            if not s.telegram_bot_token or not valid or not -30<=age<=s.telegram_auth_max_age_seconds:raise ValueError()
        except (ValueError,KeyError,TypeError):fail(401,'unauthorized','Данные Telegram недействительны')
        if user!=s.telegram_allowed_user_id:fail(403,'forbidden','Доступ закрыт')
        return issue(response,user)
    @app.get('/api/v1/me')
    def me(user=Depends(auth)):return {'user':{'telegramId':user}}
    @app.post('/api/v1/auth/logout',status_code=204)
    def logout(request:Request,response:Response,user=Depends(auth)):
        with store.transaction() as c:store.remove(c,digest(request.cookies.get('recipebook_session','')))
        response.delete_cookie('recipebook_session')
    def page(items,limit,cursor):
        try: start=int(cursor or 0)
        except ValueError:fail(422,'validation_error','Неверная страница')
        if start<0:fail(422,'validation_error','Неверная страница')
        return {'items':items[start:start+limit],'nextCursor':str(start+limit) if start+limit<len(items) else None}
    @app.get('/api/v1/recipes')
    def recipes(q:str='',totalMinutesMax:int|None=None,activeMinutesMax:int|None=None,productIds:Annotated[list[str]|None,Query()]=None,limit:int=Query(30,ge=1,le=100),cursor:str|None=None,user=Depends(auth)):
        with store.transaction() as c:rows=store.list(c,'recipe')
        out=[]
        for r in sorted(rows,key=lambda x:(x['updated'],x['id']),reverse=True):
            content=r['data']['content']; names={digest(normalize(i['name'])) for i in content['ingredients']}
            if normalize(q) not in normalize(content['title']):continue
            if totalMinutesMax is not None and content['time']['totalMinutes']>totalMinutesMax:continue
            if activeMinutesMax is not None and content['time']['activeMinutes']>activeMinutesMax:continue
            if productIds and not set(productIds)<=names:continue
            out.append({'id':r['id'],'revision':r['revision'],'updatedAt':r['updated'],**{k:content[k] for k in ('title','servings','time')}})
        return page(out,limit,cursor)
    @app.get('/api/v1/products')
    def products(q:str='',user=Depends(auth)):
        with store.transaction() as c:rows=store.list(c,'recipe')
        products={digest(normalize(i['name'])):i['name'] for r in rows for i in r['data']['content']['ingredients'] if normalize(q) in normalize(i['name'])}
        return {'items':[{'id':k,'name':v} for k,v in sorted(products.items(),key=lambda x:x[1])],'nextCursor':None}
    @app.post('/api/v1/recipes',status_code=201)
    def add_recipe(content:RecipeContent,response:Response,user=Depends(auth)):
        with store.transaction() as c:r=store.add(c,'recipe',{'content':content.model_dump(),'source':'manual','sourceConversationId':None})
        response.headers['ETag']=etag(r);return record(r)
    @app.get('/api/v1/recipes/{id}')
    def get_recipe(id:str,response:Response,user=Depends(auth)):
        with store.transaction() as c:r=fetch(c,id,'recipe')
        response.headers['ETag']=etag(r);return record(r)
    @app.put('/api/v1/recipes/{id}')
    def put_recipe(id:str,content:RecipeContent,request:Request,response:Response,user=Depends(auth)):
        with store.transaction() as c:
            r=fetch(c,id,'recipe');revision(request,r);r=store.put(c,r,{**r['data'],'content':content.model_dump()})
        response.headers['ETag']=etag(r);return record(r)
    @app.patch('/api/v1/recipes/{id}/notes')
    def notes(id:str,data:Notes,request:Request,response:Response,user=Depends(auth)):
        with store.transaction() as c:
            r=fetch(c,id,'recipe');revision(request,r)
            r=store.put(c,r,{**r['data'],'content':{**r['data']['content'],'userNotes':data.userNotes}})
        response.headers['ETag']=etag(r);return record(r)
    @app.delete('/api/v1/recipes/{id}',status_code=204)
    def remove_recipe(id:str,request:Request,user=Depends(auth)):
        with store.transaction() as c:
            r=fetch(c,id,'recipe');revision(request,r);store.remove(c,id)
    @app.post('/api/v1/conversations',status_code=201)
    def add_conversation(data:Conversation,user=Depends(auth)):
        with store.transaction() as c:
            if data.recipeId:fetch(c,data.recipeId,'recipe')
            r=store.add(c,'conversation',data.model_dump())
        return record(r)
    @app.get('/api/v1/conversations')
    def conversations(user=Depends(auth)):
        with store.transaction() as c:rows=store.list(c,'conversation')
        return {'items':[record(r) for r in reversed(rows)],'nextCursor':None}
    @app.get('/api/v1/conversations/{id}')
    def conversation(id:str,user=Depends(auth)):
        with store.transaction() as c:return record(fetch(c,id,'conversation'))
    @app.get('/api/v1/conversations/{id}/messages')
    def messages(id:str,user=Depends(auth)):
        with store.transaction() as c:
            fetch(c,id,'conversation');rows=store.list(c,'message',id)
        return {'items':[{'id':r['id'],'createdAt':r['created'],**r['data']} for r in rows],'nextCursor':None}
    @app.delete('/api/v1/conversations/{id}',status_code=204)
    def delete_conversation(id:str,user=Depends(auth)):
        with store.transaction() as c:
            fetch(c,id,'conversation')
            if any(r['data']['status']=='running' for r in store.list(c,'generation',id)):fail(409,'conversation_busy','Дождитесь ответа')
            for kind in ('message','generation','draft'):
                for r in store.list(c,kind,id):store.remove(c,r['id'])
            for r in store.list(c,'recipe'):
                if r['data']['sourceConversationId']==id:store.put(c,r,{**r['data'],'sourceConversationId':None})
            store.remove(c,id)
    @app.get('/api/v1/generation-requests/{id}')
    def generation(id:str,user=Depends(auth)):
        with store.transaction() as c:r=fetch(c,id,'generation')
        return {'requestId':id,**{k:r['data'].get(k) for k in ('status','reply','error')}}
    @app.post('/api/v1/conversations/{id}/messages')
    async def send(id:str,data:Send,user=Depends(auth)):
        rid=str(data.clientRequestId);fingerprint=digest(json.dumps(data.model_dump(mode='json'),sort_keys=True));base_id=None;base_revision=None;context=None
        with store.transaction() as c:
            fetch(c,id,'conversation');existing=store.get(c,rid,'generation')
            if existing:
                if existing['parent']!=id or existing['data']['fingerprint']!=fingerprint:fail(409,'idempotency_conflict','ID запроса уже использован')
                if existing['data']['status']=='succeeded':return existing['data']['reply']
                if existing['data']['status']=='running':return JSONResponse(status_code=202,content={'requestId':rid,'status':'running'})
                fail(502,'model_error',existing['data'].get('error') or 'Запрос прерван')
            if any(r['data']['status']=='running' for r in store.list(c,'generation',id)):fail(409,'conversation_busy','Дождитесь предыдущего ответа')
            if data.recipeContext:
                ctx=data.recipeContext
                if 'draftId' in ctx:
                    d=fetch(c,ctx['draftId'],'draft')
                    if d['parent']!=id:fail(422,'validation_error','Черновик из другого чата')
                    context=d['data']['content'];base_id=d['data']['baseRecipeId'];base_revision=d['data']['baseRevision']
                elif 'recipeId' in ctx:
                    r=fetch(c,ctx['recipeId'],'recipe')
                    if ctx.get('revision')!=r['revision']:fail(412,'revision_conflict','Рецепт изменился')
                    base_id=r['id'];base_revision=r['revision'];context=r['data']['content']
                else:fail(422,'validation_error','Неверный контекст')
            text=data.text
            if data.selectedIdea:
                m=fetch(c,data.selectedIdea.get('messageId',''),'message')
                ideas=(m['data'].get('reply') or {}).get('ideas',[])
                selected=next((x for x in ideas if x['id']==data.selectedIdea.get('ideaId')),None)
                if m['parent']!=id or not selected:fail(422,'validation_error','Идея не найдена')
                text+='\nВыбранная идея: '+selected['title']+' '+selected['description']
            old=store.list(c,'message',id);history=[]
            for i in range(len(old)-1):
                a,b=old[i]['data'],old[i+1]['data']
                if a['role']=='user' and b['role']=='assistant':history.append([('human',a['text']),('ai',b['reply']['message'])])
            um=store.add(c,'message',{'role':'user','text':data.text,'reply':None},parent=id)
            store.add(c,'generation',{'status':'running','fingerprint':fingerprint,'reply':None,'error':None},parent=id,id=rid)
        try:
            result=await model.reply(text,history,context)
        except Exception as exc:
            error='Модель недоступна. Проверьте настройки сервера.' if isinstance(exc,ValueError) else 'Не удалось получить полный ответ модели. Попробуйте ещё раз.'
            with store.transaction() as c:
                r=fetch(c,rid,'generation');store.put(c,r,{**r['data'],'status':'failed','error':error})
            fail(504 if isinstance(exc,TimeoutError) else 502,'model_error',error)
        with store.transaction() as c:
            draft=None
            if result.recipe:
                content={'schemaVersion':1,**result.recipe.model_dump()}
                d=store.add(c,'draft',{'content':content,'baseRecipeId':base_id,'baseRevision':base_revision,'savedId':None,'savedTarget':None},parent=id)
                draft={'id':d['id'],'content':content,'baseRecipeId':base_id,'baseRevision':base_revision}
            mid=uid();reply={'requestId':rid,'userMessageId':um['id'],'assistantMessageId':mid,'createdAt':now(),'message':result.message,'ideas':[{'id':uid(),**x.model_dump()} for x in result.ideas],'recipeDraft':draft}
            store.add(c,'message',{'role':'assistant','text':None,'reply':reply},parent=id,id=mid)
            r=fetch(c,rid,'generation');store.put(c,r,{**r['data'],'status':'succeeded','reply':reply})
        return reply
    @app.post('/api/v1/drafts/{id}/save')
    def save_draft(id:str,data:Save,request:Request,response:Response,user=Depends(auth)):
        key=request.headers.get('idempotency-key','')
        try:UUID(key)
        except ValueError:fail(422,'validation_error','Нужен ключ сохранения')
        fingerprint=digest(json.dumps([id,data.targetRecipeId,request.headers.get('if-match')]))
        with store.transaction() as c:
            previous=store.get(c,key,'saved')
            if previous:
                if previous['data']['fingerprint']!=fingerprint:fail(409,'idempotency_conflict','Ключ уже использован')
                response.status_code=previous['data']['status'];return previous['data']['record']
            d=fetch(c,id,'draft');v=d['data']
            if v['savedId']:
                if v['savedTarget']!=data.targetRecipeId:fail(409,'draft_already_saved','Черновик уже сохранён')
                r=store.get(c,v['savedId'],'recipe')
                if not r:fail(409,'saved_recipe_deleted','Сохранённый рецепт удалён')
                return record(r)
            if data.targetRecipeId:
                r=fetch(c,data.targetRecipeId,'recipe');revision(request,r)
                if v['baseRecipeId']!=r['id'] or v['baseRevision']!=r['revision']:fail(412,'revision_conflict','Черновик относится к другой версии')
                content=RecipeContent.model_validate({**v['content'],'userNotes':r['data']['content']['userNotes']})
                r=store.put(c,r,{**r['data'],'content':content.model_dump()});status=200
            else:
                content=RecipeContent.model_validate({**v['content'],'userNotes':''})
                r=store.add(c,'recipe',{'content':content.model_dump(),'source':'llm','sourceConversationId':d['parent']});status=201
            store.put(c,d,{**v,'savedId':r['id'],'savedTarget':data.targetRecipeId})
            store.add(c,'saved',{'fingerprint':fingerprint,'record':record(r),'status':status},id=key)
        response.status_code=status;response.headers['ETag']=etag(r);return record(r)
    return app

app=create_app()
