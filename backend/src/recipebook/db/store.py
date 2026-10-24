from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, JSON, select, update, delete, event
from sqlalchemy.pool import StaticPool

metadata=MetaData()
records=Table('records',metadata,Column('id',String,primary_key=True),Column('kind',String,index=True),
    Column('parent',String,index=True),Column('revision',Integer,nullable=False),Column('data',JSON,nullable=False),
    Column('created',String,nullable=False),Column('updated',String,nullable=False))

def now(): return datetime.now(timezone.utc).isoformat()

def uid(): return str(uuid4())

class Store:
    def __init__(self,url):
        if not url.startswith('sqlite:'): raise ValueError('This version uses SQLite')
        if url.startswith('sqlite:///') and ':memory:' not in url:
            Path(url.removeprefix('sqlite:///')).parent.mkdir(parents=True,exist_ok=True)
        self.engine=create_engine(url,connect_args={'check_same_thread':False,'timeout':10},
                                 **({'poolclass':StaticPool} if ':memory:' in url else {}))
        @event.listens_for(self.engine,'connect')
        def configure(dbapi,_):
            dbapi.execute('PRAGMA journal_mode=WAL')
        metadata.create_all(self.engine)
    @contextmanager
    def transaction(self):
        with self.engine.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            try: yield c; c.commit()
            except BaseException: c.rollback(); raise
    def get(self,c,id,kind=None):
        query=select(records).where(records.c.id==id)
        if kind: query=query.where(records.c.kind==kind)
        row=c.execute(query).mappings().first()
        return dict(row) if row else None
    def list(self,c,kind,parent=None):
        q=select(records).where(records.c.kind==kind).order_by(records.c.created,records.c.id)
        if parent is not None:q=q.where(records.c.parent==parent)
        return [dict(r) for r in c.execute(q).mappings()]
    def add(self,c,kind,data,parent=None,id=None):
        row=dict(id=id or uid(),kind=kind,parent=parent,revision=1,data=data,created=now(),updated=now())
        c.execute(records.insert().values(**row)); return row
    def put(self,c,row,data):
        row={**row,'data':data,'revision':row['revision']+1,'updated':now()}
        c.execute(update(records).where(records.c.id==row['id']).values(**row));return row
    def remove(self,c,id):c.execute(delete(records).where(records.c.id==id))
