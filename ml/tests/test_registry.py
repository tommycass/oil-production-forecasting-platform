"""Tests de la decisión de promoción a Production (criterio ADR-040, Rol 3 1.5).

Se testea la **función pura** ``promotion_decision`` en aislamiento (sin MLflow ni
datos): dado el test RMSE del candidato, el de la persistencia y el del Production
actual, decide si promueve. Es la regla de negocio del despliegue automático del
modelo, así que conviene fijarla con tests.
"""
from ml.registry import promotion_decision


def test_no_promueve_si_no_supera_persistencia():
    """La vara de éxito (ADR-029): sin superar la persistencia, no se promueve —
    aunque no haya Production actual."""
    promover, motivo = promotion_decision(
        nuevo_test_rmse=170.0, persistencia_test_rmse=166.0, prod_test_rmse=None
    )
    assert promover is False
    assert "persistencia" in motivo


def test_promueve_primer_campeon_si_supera_persistencia():
    """Sin Production previo y superando la persistencia: se promueve el primer campeón."""
    promover, motivo = promotion_decision(
        nuevo_test_rmse=150.0, persistencia_test_rmse=166.0, prod_test_rmse=None
    )
    assert promover is True
    assert "Production" in motivo


def test_promueve_si_mejora_al_production_actual():
    """Con Production previo: promueve solo si mejora su test RMSE."""
    promover, _ = promotion_decision(
        nuevo_test_rmse=150.0, persistencia_test_rmse=166.0, prod_test_rmse=154.0
    )
    assert promover is True


def test_no_promueve_si_no_mejora_al_production_actual():
    """Supera la persistencia pero no mejora al Production actual: no se promueve."""
    promover, motivo = promotion_decision(
        nuevo_test_rmse=160.0, persistencia_test_rmse=166.0, prod_test_rmse=154.0
    )
    assert promover is False
    assert "no mejora" in motivo


def test_empate_no_promueve():
    """Empate estricto (no es 'mejor que'): no se promueve (criterio conservador)."""
    promover, _ = promotion_decision(
        nuevo_test_rmse=154.0, persistencia_test_rmse=166.0, prod_test_rmse=154.0
    )
    assert promover is False
