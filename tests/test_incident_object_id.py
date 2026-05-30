from alarm_manager_server.models.incident import Incident, IncidentOwner, incident_object_id


def test_incident_object_id_prefers_entity_id():
    inc = Incident(
        id="i1",
        title="t",
        severity=1,
        status=1,
        started_at="2025-01-01T00:00:00+00:00",
        entity_id="entity-1",
        owner=IncidentOwner(_id="owner-1", name="Host"),
    )
    assert incident_object_id(inc) == "entity-1"


def test_incident_object_id_falls_back_to_owner_id():
    inc = Incident(
        id="i1",
        title="t",
        severity=1,
        status=1,
        started_at="2025-01-01T00:00:00+00:00",
        entity_id="",
        owner=IncidentOwner(_id="owner-1", name="Host"),
    )
    assert incident_object_id(inc) == "owner-1"
