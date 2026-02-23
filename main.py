from fastapi import FastAPI

app = FastAPI(title="GLPI Scanner API")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API del escáner funcionando"}