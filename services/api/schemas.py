from pydantic import BaseModel, Field, ConfigDict
from typing import Any, Literal
class ProjectCreate(BaseModel):
    name:str=Field(min_length=1,max_length=240)
    description:str=''
    goal:str=''
    mode:Literal['auto','assisted','manual']|None=None
    budget:dict=Field(default_factory=lambda:{'allow_paid':False})
    config:dict=Field(default_factory=dict)
class GraphCommand(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id:str|None=None
    request_id:str=Field(min_length=1,max_length=160)
    expected_revision:int
    operation:str
    targets:list[str]=Field(default_factory=list)
    params:dict=Field(default_factory=dict)
    run:bool=False
class RunRequest(BaseModel):
    request_id:str|None=None
    scope:Literal['single','ancestors','descendants','affected','to_here','from_here','subtree']='single'
    config:dict=Field(default_factory=dict)
class ResourceCreate(BaseModel):
    project_id:str
    title:str=''
    data:dict=Field(default_factory=dict)
class FileWrite(BaseModel):
    path:str=Field(min_length=1,max_length=2000)
    content:str=Field(max_length=10_000_000)
    expected_revision:int|None=None
    create_only:bool=False
