import os
import unittest
from unittest.mock import patch

from estado_cognitivo import (
    limpiar_todo_para_pruebas,
    obtener_estado,
    registrar_entrada,
    resumen_seguro_para_modelo,
)
from personalidad import construir_contexto_identidad
from reflexion import proponer_reflexion


class IdentidadYReflexionTest(unittest.TestCase):
    def setUp(self):
        limpiar_todo_para_pruebas()

    def test_identidad_esta_inerte_si_bandera_esta_apagada(self):
        with patch.dict(os.environ, {"DEISY_IDENTIDAD_ACTIVA": "false"}):
            texto = construir_contexto_identidad(
                {"modo": "Personal"},
                {"modo": "conversacion"},
            )
        self.assertEqual(texto, "")

    def test_identidad_es_estilo_y_no_autorizacion(self):
        with patch.dict(os.environ, {"DEISY_IDENTIDAD_ACTIVA": "true"}):
            texto = construir_contexto_identidad(
                {"modo": "Trabajo"},
                {"modo": "depuracion"},
            )
        self.assertIn("IDENTIDAD CONVERSACIONAL", texto)
        self.assertIn("no autoriza acciones", texto.lower())

    def test_estado_detecta_depuracion_sin_guardar_texto_crudo(self):
        estado = registrar_entrada(
            "telegram_100",
            "Tengo un Traceback al compilar Python y una contrasena=secreta",
        )
        self.assertEqual(estado["tema"], "depuracion_tecnica")
        resumen = resumen_seguro_para_modelo(estado)
        self.assertIn("depuracion_tecnica", resumen)
        self.assertNotIn("secreta", resumen)
        self.assertNotIn("contrasena", resumen.lower())

    def test_sesiones_distintas_no_comparten_tema(self):
        registrar_entrada("telegram_100", "Tengo un error de Python")
        registrar_entrada("omen_principal", "hola")
        self.assertEqual(obtener_estado("telegram_100")["modo"], "depuracion")
        self.assertEqual(obtener_estado("omen_principal")["modo"], "conversacion")

    def test_reflexion_no_aplica_nada_y_pide_aprobacion(self):
        with patch.dict(os.environ, {"DEISY_REFLEXION_ACTIVA": "true"}):
            propuesta = proponer_reflexion(
                "Quiero que puedas leer notificaciones de la Omen"
            )
        self.assertIsNotNone(propuesta)
        self.assertEqual(propuesta["tipo"], "funcion_futura")
        self.assertTrue(propuesta["requiere_aprobacion"])
        self.assertEqual(propuesta["estado"], "propuesta_no_aplicada")

    def test_charla_normal_no_crea_propuesta(self):
        with patch.dict(os.environ, {"DEISY_REFLEXION_ACTIVA": "true"}):
            propuesta = proponer_reflexion("Hola Deisy, como estas?")
        self.assertIsNone(propuesta)


if __name__ == "__main__":
    unittest.main()