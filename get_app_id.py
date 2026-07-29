from bifrost import create_app
from config import Config
from bson import ObjectId

app = create_app(Config)
with app.app_context():
    from bifrost.models.apps import BifrostDB
    db = BifrostDB()
    apps = list(db.db.applications.find({"app_name": {"$regex": "Finance", "$options": "i"}}))
    for a in apps:
        print(a["_id"])
