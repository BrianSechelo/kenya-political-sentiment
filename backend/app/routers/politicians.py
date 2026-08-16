from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import Politician
from app.schemas.politician import (
    PoliticianCreate,
    PoliticianUpdate,
    PoliticianResponse,
)
from fastapi import APIRouter, Depends, HTTPException, status


router = APIRouter(
    prefix="/politicians",
    tags=["Politicians"],
)

@router.get("/", response_model=list[PoliticianResponse])
def get_politicians(db: Session = Depends(get_db)):
    politicians = db.query(Politician).all()
    return politicians

@router.get("/{politician_id}", response_model=PoliticianResponse)
def get_politician(
    politician_id: int,
    db: Session = Depends(get_db)
):
    politician = (
        db.query(Politician)
        .filter(Politician.politician_id == politician_id)
        .first()
    )

    if politician is None:
        raise HTTPException(
            status_code=404,
            detail="Politician not found"
        )

    return politician

@router.patch("/{politician_id}", response_model=PoliticianResponse)
def update_politician(
    politician_id: int,
    politician_update: PoliticianUpdate,
    db: Session = Depends(get_db),
):
    politician = (
        db.query(Politician)
        .filter(Politician.politician_id == politician_id)
        .first()
    )

    if politician is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Politician not found",
        )

    update_data = politician_update.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(politician, field, value)

    try:
        db.commit()
        db.refresh(politician)
        return politician

    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A politician with this slug already exists",
        )

@router.delete("/{politician_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_politician(
    politician_id: int,
    db: Session = Depends(get_db),
):
    politician = (
        db.query(Politician)
        .filter(Politician.politician_id == politician_id)
        .first()
    )

    if politician is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Politician not found",
        )

    db.delete(politician)
    db.commit()

    return None
    
@router.post("/", response_model=PoliticianResponse, status_code=201)
def create_politician(
    politician: PoliticianCreate,
    db: Session = Depends(get_db)
):
    new_politician = Politician(**politician.model_dump())

    db.add(new_politician)
    try:
        db.commit()
        db.refresh(new_politician)
    except IntegrityError:
        db.rollback()

    raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A politician with this slug already exists",
        )

    return new_politician