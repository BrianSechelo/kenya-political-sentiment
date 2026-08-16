from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Politician, PoliticianAlias
from app.schemas.politician_alias import (
    PoliticianAliasCreate,
    PoliticianAliasUpdate,
    PoliticianAliasResponse,
)


router = APIRouter(
    prefix="/politician-aliases",
    tags=["Politician Aliases"],
)

@router.post(
    "/",
    response_model=PoliticianAliasResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_politician_alias(
    alias_data: PoliticianAliasCreate,
    db: Session = Depends(get_db),
):
    politician = (
        db.query(Politician)
        .filter(Politician.politician_id == alias_data.politician_id)
        .first()
    )

    if politician is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Politician not found",
        )

    existing_alias = (
    db.query(PoliticianAlias)
    .filter(
        PoliticianAlias.politician_id == alias_data.politician_id,
        PoliticianAlias.alias == alias_data.alias,
    )
    .first()
)

    if existing_alias:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This alias already exists for this politician",
             )

    new_alias = PoliticianAlias(
        politician_id=alias_data.politician_id,
        alias=alias_data.alias,
        alias_type=alias_data.alias_type,
    )

    db.add(new_alias)
    db.commit()
    db.refresh(new_alias)

    return new_alias

@router.get("/", response_model=list[PoliticianAliasResponse])
def get_politician_aliases(
    db: Session = Depends(get_db),
):
    aliases = db.query(PoliticianAlias).all()
    return aliases

@router.get(
    "/politician/{politician_id}",
    response_model=list[PoliticianAliasResponse],
)
def get_aliases_by_politician(
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

    aliases = (
        db.query(PoliticianAlias)
        .filter(PoliticianAlias.politician_id == politician_id)
        .all()
    )

    return aliases
@router.patch(
    "/{alias_id}",
    response_model=PoliticianAliasResponse,
)
def update_politician_alias(
    alias_id: int,
    alias_data: PoliticianAliasUpdate,
    db: Session = Depends(get_db),
):
    existing_alias = (
        db.query(PoliticianAlias)
        .filter(PoliticianAlias.alias_id == alias_id)
        .first()
    )

    if existing_alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alias not found",
        )

    # If alias is being changed, make sure the politician
    # doesn't already have the new alias.
    if alias_data.alias is not None:
        duplicate = (
            db.query(PoliticianAlias)
            .filter(
                PoliticianAlias.politician_id == existing_alias.politician_id,
                PoliticianAlias.alias == alias_data.alias,
                PoliticianAlias.alias_id != alias_id,
            )
            .first()
        )

        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This alias already exists for this politician",
            )

    update_data = alias_data.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(existing_alias, field, value)

    db.commit()
    db.refresh(existing_alias)

    return existing_alias

@router.delete("/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alias(
    alias_id: int,
    db: Session = Depends(get_db),
):
    alias = (
        db.query(PoliticianAlias)
        .filter(PoliticianAlias.alias_id == alias_id)
        .first()
    )

    if alias is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alias not found",
        )

    db.delete(alias)
    db.commit()

    return None