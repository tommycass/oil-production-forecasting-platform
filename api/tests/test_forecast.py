def test_forecast_returns_200(client, auth_headers):
    response = client.get(
        "/api/v1/forecast",
        params={
            "id_well": "POZO-001",
            "date_start": "2024-01-01",
            "date_end": "2024-01-07",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200


def test_forecast_response_has_id_well_and_data(client, auth_headers):
    response = client.get(
        "/api/v1/forecast",
        params={
            "id_well": "POZO-001",
            "date_start": "2024-01-01",
            "date_end": "2024-01-07",
        },
        headers=auth_headers,
    )
    body = response.json()
    assert "id_well" in body
    assert "data" in body
    assert body["id_well"] == "POZO-001"


def test_forecast_data_items_have_correct_fields(client, auth_headers):
    response = client.get(
        "/api/v1/forecast",
        params={
            "id_well": "POZO-001",
            "date_start": "2024-01-01",
            "date_end": "2024-01-03",
        },
        headers=auth_headers,
    )
    data = response.json()["data"]
    assert isinstance(data, list)
    for point in data:
        assert "date" in point
        assert "prod" in point
        assert isinstance(point["prod"], float)


def test_forecast_missing_params_returns_422(client, auth_headers):
    response = client.get("/api/v1/forecast", headers=auth_headers)
    assert response.status_code == 422