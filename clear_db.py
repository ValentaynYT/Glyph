from app import app, db, Shelf

with app.app_context():
   
    db.session.query(Shelf).delete()
    db.session.commit()
    print("Все полки успешно удалены из базы данных.")
