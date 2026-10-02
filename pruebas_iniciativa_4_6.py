import os
import unittest
from unittest.mock import patch

from iniciativa import (
    DecisionIniciativa,
    contexto_iniciativa_para_modelo,
    decidir_iniciativa,
    limpiar_iniciativa_para_pruebas,
    obtener_hilo,
    registrar_turno_conversacional,
)


def _estado(tema="conversacion_general", objetivo="conversar_con_claridad", modo="conversacion"):
    return {"tema": tema, "objetivo": objetivo, "modo": modo}


class IniciativaConversacionalTest(unittest.TestCase):
    def setUp(self):
        self.entorno = patch.dict(
            os.environ,
            {
                "DEISY_INICIATIVA_ACTIVA": "true",
                "DEISY_INICIATIVA_COOLDOWN": "600",
                "DEISY_INICIATIVA_HILO_TTL": "1800",
            },
            clear=False,
        )
        self.entorno.start()
        self.addCleanup(self.entorno.stop)
        limpiar_iniciativa_para_pruebas()

    def tearDown(self):
        limpiar_iniciativa_para_pruebas()

    def test_charla_relevante_puede_proponer_algo(self):
        decision = decidir_iniciativa(
            "telegram:1",
            "Voy camino al trabajo y está lloviendo.",
            _estado(),
            {"modo": "Trayecto"},
        )
        self.assertTrue(decision.intervenir)
        self.assertEqual(decision.tipo, "propuesta")
        self.assertGreaterEqual(decision.relevancia, 7)
        self.assertIn("paraguas", decision.contenido.lower())

    def test_puede_retornar_un_hilo_relacionado(self):
        estado = _estado(
            "depuracion_tecnica",
            "entender_o_corregir_problema",
            "depuracion",
        )
        registrar_turno_conversacional("telegram:2", estado, None, True)
        decision = decidir_iniciativa("telegram:2", "vale", estado, {"modo": "Personal"})
        self.assertTrue(decision.intervenir)
        self.assertEqual(decision.tipo, "pregunta")
        self.assertIn("siguiente paso", decision.contenido.lower())

    def test_no_recupera_hilo_irrelevante(self):
        estado_tecnico = _estado(
            "depuracion_tecnica",
            "entender_o_corregir_problema",
            "depuracion",
        )
        registrar_turno_conversacional("telegram:3", estado_tecnico, None, True)
        decision = decidir_iniciativa(
            "telegram:3",
            "Hoy solo quería saludar.",
            _estado(),
            {"modo": "Personal"},
        )
        self.assertFalse(decision.intervenir)

    def test_sabe_callarse_en_charla_general(self):
        decision = decidir_iniciativa(
            "telegram:4", "hola", _estado(), {"modo": "Personal"}
        )
        self.assertFalse(decision.intervenir)
        self.assertEqual(decision.tipo, "ninguna")

    def test_no_repite_la_misma_propuesta(self):
        estado = _estado()
        primera = decidir_iniciativa(
            "telegram:5", "Voy camino a casa y llueve.", estado, {"modo": "Trayecto"}
        )
        self.assertTrue(primera.intervenir)
        registrar_turno_conversacional("telegram:5", estado, primera, True)
        segunda = decidir_iniciativa(
            "telegram:5", "Voy camino a casa y llueve.", estado, {"modo": "Trayecto"}
        )
        self.assertFalse(segunda.intervenir)

    def test_una_propuesta_no_es_un_comando(self):
        decision = decidir_iniciativa(
            "telegram:6", "Voy camino al trabajo y llueve.", _estado(), {"modo": "Trayecto"}
        )
        self.assertIsInstance(decision, DecisionIniciativa)
        contexto = contexto_iniciativa_para_modelo(decision).lower()
        for prefijo in ("sistema:", "buscar:", "tecla:", "iphone:", "red:", "start "):
            self.assertNotIn(prefijo, contexto)

    def test_hilo_no_guarda_la_frase_literal(self):
        estado = _estado(
            "aprendizaje_tecnico", "aprender_un_concepto", "aprendizaje"
        )
        registrar_turno_conversacional("telegram:7", estado, None, True)
        hilo = obtener_hilo("telegram:7")
        self.assertEqual(hilo["tema"], "aprendizaje_tecnico")
        self.assertNotIn("frase", repr(hilo).lower())


if __name__ == "__main__":
    unittest.main()
    