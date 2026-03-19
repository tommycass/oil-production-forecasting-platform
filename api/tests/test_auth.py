def test_missing_api_key_returns_403(client):
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
    )
    assert response.status_code == 403


def test_invalid_api_key_returns_403(client, bad_headers):
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
        headers=bad_headers,
    )
    assert response.status_code == 403


def test_valid_api_key_is_accepted(client, auth_headers):
    response = client.get(
        "/api/v1/wells",
        params={"date_query": "2024-01-01"},
        headers=auth_headers,
    )
    assert response.status_code != 403