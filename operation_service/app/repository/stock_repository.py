from sqlalchemy.orm import Session

from app.models.stock_movement import StockMovement


def create_movement(db: Session, movement: StockMovement, commit: bool = True) -> StockMovement:
    db.add(movement)
    if commit:
        db.commit()
        db.refresh(movement)
    else:
        db.flush()
    return movement
