def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_list_projects(client):
    created = client.post("/projects", json={"name": "Apollo", "description": "Moonshot"})
    assert created.status_code == 201
    assert client.get("/projects").json()[0]["name"] == "Apollo"


def test_duplicate_project_name_conflicts(client):
    client.post("/projects", json={"name": "Apollo"})
    assert client.post("/projects", json={"name": "Apollo"}).status_code == 409


def test_items_lifecycle(client):
    project = client.post("/projects", json={"name": "Gemini"}).json()
    item = client.post(f"/projects/{project['id']}/items", json={"title": "Design capsule"}).json()
    assert item["status"] == "todo"
    updated = client.patch(f"/items/{item['id']}", json={"status": "done"})
    assert updated.json()["status"] == "done"


def test_missing_project_returns_404(client):
    assert client.get("/projects/999").status_code == 404
