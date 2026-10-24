import copy
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from recipebook.config import Settings
from recipebook.domain.recipe import RecipeContent
from recipebook.main import create_app

EXAMPLE=json.loads((Path(__file__).resolve().parents[2]/'schemas/recipe.example.json').read_text())
ORIGIN={'Origin':'http://localhost:5173'}
@pytest.fixture
def client(tmp_path):
    s=Settings(_env_file=None,database_url='sqlite:///'+str(tmp_path/'test.db'),dev_auth_enabled=True,llm_provider='fake')
    with TestClient(create_app(s)) as c:
        c.headers.update(ORIGIN)
        assert c.post('/api/v1/auth/dev').status_code==200
        yield c

def create(c):
    r=c.post('/api/v1/recipes',json=EXAMPLE)
    assert r.status_code==201,r.text
    return r.json(),r.headers['etag']

def test_manual_crud_and_conflict(client):
    r,tag=create(client)
    assert client.get('/api/v1/recipes?q=ПАСТА').json()['items'][0]['id']==r['id']
    assert not client.get('/api/v1/recipes?totalMinutesMax=1').json()['items']
    path='/api/v1/recipes/'+r['id']
    assert client.put(path,json=EXAMPLE).status_code==428
    response=client.patch(path+'/notes',json={'userNotes':'Мои заметки'},headers={'If-Match':tag})
    assert response.status_code==200
    assert client.put(path,json=EXAMPLE,headers={'If-Match':tag}).status_code==412
    assert client.get(path).json()['content']['userNotes']=='Мои заметки'
    assert client.delete(path,headers={'If-Match':response.headers['etag']}).status_code==204
    assert client.get(path).status_code==404

def test_invalid_recipe(client):
    content=copy.deepcopy(EXAMPLE);content['time']['activeMinutes']=999
    assert client.post('/api/v1/recipes',json=content).status_code==422
    content=copy.deepcopy(EXAMPLE);content['steps'][0]['ingredientIds']=['missing']
    with pytest.raises(ValidationError):RecipeContent.model_validate(content)

def test_auth_and_origin(tmp_path):
    s=Settings(_env_file=None,database_url='sqlite:///'+str(tmp_path/'test.db'))
    with TestClient(create_app(s)) as c:
        assert c.get('/api/v1/recipes').status_code==401
        assert c.post('/api/v1/auth/dev',headers=ORIGIN).status_code==403
        assert c.post('/api/v1/recipes',json=EXAMPLE,headers={'Origin':'https://evil.example'}).status_code==403

def test_telegram(tmp_path):
    token='test-token';s=Settings(_env_file=None,database_url='sqlite:///'+str(tmp_path/'test.db'),telegram_bot_token=token,telegram_allowed_user_id=42)
    fields={'auth_date':str(int(time.time())),'user':json.dumps({'id':42})}
    check='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    secret=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    fields['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    with TestClient(create_app(s)) as c:
        assert c.post('/api/v1/auth/telegram',json={'initData':urlencode(fields)},headers=ORIGIN).status_code==200
        assert c.get('/api/v1/me').json()['user']['telegramId']==42
        fields['auth_date']='0'
        assert c.post('/api/v1/auth/telegram',json={'initData':urlencode(fields)},headers=ORIGIN).status_code==401

def test_chat_save_preserves_notes_and_retry(client):
    r,tag=create(client)
    chat=client.post('/api/v1/conversations',json={'recipeId':r['id']}).json()['id']
    body={'clientRequestId':str(uuid4()),'text':'Измени рецепт','selectedIdea':None,'recipeContext':{'recipeId':r['id'],'revision':r['revision']}}
    response=client.post(f'/api/v1/conversations/{chat}/messages',json=body)
    assert response.status_code==200,response.text
    reply=response.json()
    assert client.post(f'/api/v1/conversations/{chat}/messages',json=body).json()==reply
    d=reply['recipeDraft'];assert 'userNotes' not in d['content']
    headers={'If-Match':tag,'Idempotency-Key':str(uuid4())}
    result=client.post('/api/v1/drafts/'+d['id']+'/save',json={'targetRecipeId':r['id']},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['content']['userNotes']==EXAMPLE['userNotes']
    assert client.post('/api/v1/drafts/'+d['id']+'/save',json={'targetRecipeId':r['id']},headers=headers).json()==result.json()
    assert len(client.get(f'/api/v1/conversations/{chat}/messages').json()['items'])==2

def test_stale_draft(client):
    r,tag=create(client);chat=client.post('/api/v1/conversations',json={}).json()['id']
    result=client.post(f'/api/v1/conversations/{chat}/messages',json={'clientRequestId':str(uuid4()),'text':'Рецепт','recipeContext':{'recipeId':r['id'],'revision':1}})
    d=result.json()['recipeDraft']
    client.patch('/api/v1/recipes/'+r['id']+'/notes',json={'userNotes':'new'},headers={'If-Match':tag})
    assert client.post('/api/v1/drafts/'+d['id']+'/save',json={'targetRecipeId':r['id']},headers={'If-Match':tag,'Idempotency-Key':str(uuid4())}).status_code==412

def test_canonical_example():
    import jsonschema
    schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/recipe.schema.json').read_text())
    jsonschema.validate(EXAMPLE,schema)
    assert RecipeContent.model_validate(EXAMPLE).title==EXAMPLE['title']
