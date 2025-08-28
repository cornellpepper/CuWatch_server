from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from config import Config

db = SQLAlchemy()

def init_db(app: Flask):
    app.config.from_object(Config)
    db.init_app(app)
    return app
