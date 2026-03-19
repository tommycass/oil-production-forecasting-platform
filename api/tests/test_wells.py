def test_wells_returns_200(client, auth_headers):
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
        headers=auth_headers,
    )
    assert response.status_code == 200


def test_wells_returns_list(client, auth_headers):
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
        headers=auth_headers,
    )
    data = response.json()
    assert isinstance(data, list)
    # Cada elemento debe tener id_well
    for well in data:
        assert "id_well" in well


def test_wells_missing_date_query_returns_422(client, auth_headers):
    response = client.get("/api/v1/wells", headers=auth_headers)
    assert response.status_code == 422