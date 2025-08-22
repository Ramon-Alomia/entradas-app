from app import app

def test_health_ok():
    client = app.test_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json().get("status") == "ok"
