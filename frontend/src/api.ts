export class ApiError extends Error {constructor(message:string,public status:number){super(message)}}
export async function api<T=any>(path:string,method='GET',body?:unknown,headers:Record<string,string>={}):Promise<T>{
 const response=await fetch('/api/v1'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json',...headers},body:body===undefined?undefined:JSON.stringify(body)});
 if(!response.ok){const error=await response.json().catch(()=>({}));throw new ApiError(error.error?.message||'Не удалось выполнить запрос',response.status)}
 if(response.status===204)return undefined as T;
 return response.json();
}
export const revision=(r:{id:string;revision:number})=>({'If-Match':`"recipe:${r.id}:${r.revision}"`});
