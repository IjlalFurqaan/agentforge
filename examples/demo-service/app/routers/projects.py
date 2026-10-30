from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Item, Project
from app.schemas import ItemCreate, ItemOut, ItemUpdate, ProjectCreate, ProjectOut

router = APIRouter()


def _project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[Project]:
    return list(db.scalars(select(Project).order_by(Project.id)))


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> Project:
    if db.scalar(select(Project).where(Project.name == body.name)):
        raise HTTPException(status_code=409, detail="Project name already exists")
    project = Project(name=body.name, description=body.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)) -> Project:
    return _project_or_404(db, project_id)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> None:
    db.delete(_project_or_404(db, project_id))
    db.commit()


@router.get("/projects/{project_id}/items", response_model=list[ItemOut])
def list_items(project_id: int, db: Session = Depends(get_db)) -> list[Item]:
    _project_or_404(db, project_id)
    return list(db.scalars(select(Item).where(Item.project_id == project_id).order_by(Item.id)))


@router.post("/projects/{project_id}/items", response_model=ItemOut, status_code=status.HTTP_201_CREATED)
def add_item(project_id: int, body: ItemCreate, db: Session = Depends(get_db)) -> Item:
    _project_or_404(db, project_id)
    item = Item(project_id=project_id, title=body.title)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/items/{item_id}", response_model=ItemOut)
def update_item(item_id: int, body: ItemUpdate, db: Session = Depends(get_db)) -> Item:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    item.status = body.status
    db.commit()
    db.refresh(item)
    return item
