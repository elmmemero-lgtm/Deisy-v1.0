import os
import unittest
from unittest.mock import patch

import api_deisy
from iniciativa import DecisionIniciativa


class ContratoIniciativaApiTest(unittest.TestCase):
    def test_charla_temprana_pasa_su_sesion_a_cerebro(self):
        with (
            patch.object(api_deisy, "_requiere_token", return_value=None),
            patch.object(
                api_deisy,
                "generar_respuesta_charla",
                return_value=(True, "Hola."),
            ) as charla,
            patch.object(api_deisy, "guardar_historial"),
            patch.object(api_deisy, "recuperar_memoria_para_charla", return_value=""),
            patch.object(api_deisy, "sugerencia_sueno_si_toca", return_value=""),
        ):
            respuesta = self.cliente.post(
                "/preguntar",
                json={
                    "mensaje": "hola",
                    "dispositivo": "telegram",
                    "sesion": "prueba:aislada",
                },
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(charla.call_args.kwargs["sesion"], "prueba:aislada")

    def setUp(self):
        self.entorno = patch.dict(
            os.environ,
            {"DEISY_INICIATIVA_ACTIVA": "true"},
            clear=False,
        )
        self.entorno.start()
        self.addCleanup(self.entorno.stop)
        self.cliente = api_deisy.app.test_client()

    def test_una_accion_sigue_llegando_al_ejecutor_sin_iniciativa(self):
        with (
            patch.object(api_deisy, "_requiere_token", return_value=None),
            patch.object(api_deisy, "_ejecutar_comando", return_value=("Volumen subido.", True, "accion_omen")) as ejecutar,
            patch.object(api_deisy, "decidir_iniciativa") as decidir,
            patch.object(api_deisy, "guardar_historial"),
            patch.object(api_deisy, "sugerencia_sueno_si_toca", return_value=""),
        ):
            respuesta = self.cliente.post(
                "/preguntar",
                json={"mensaje": "sube el volumen", "dispositivo": "prueba"},
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(ejecutar.called)
        self.assertEqual(ejecutar.call_args.args[0], "sistema: subir_volumen")
        decidir.assert_not_called()

    def test_una_propuesta_de_charla_no_ejecuta_orden(self):
        propuesta = DecisionIniciativa(
            intervenir=True,
            tipo="propuesta",
            contenido="Se me ocurre una alternativa útil.",
            motivo="prueba",
            relevancia=8,
        )
        with (
            patch.object(api_deisy, "_requiere_token", return_value=None),
            patch.object(api_deisy, "procesar_instruccion", return_value=["charla: necesito una idea"]),
            patch.object(api_deisy, "filtrar_comandos_router", return_value=["charla: necesito una idea"]),
            patch.object(api_deisy, "decidir_iniciativa", return_value=propuesta),
            patch.object(api_deisy, "generar_respuesta_charla", return_value=(True, "Respuesta de charla.")) as charla,
            patch.object(api_deisy, "enviar_orden_a_omen") as orden_omen,
            patch.object(api_deisy, "guardar_historial"),
            patch.object(api_deisy, "sugerencia_sueno_si_toca", return_value=""),
        ):
            respuesta = self.cliente.post(
                "/preguntar",
                json={"mensaje": "necesito una idea", "dispositivo": "prueba"},
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(charla.called)
        self.assertIn("contexto_iniciativa", charla.call_args.kwargs)
        orden_omen.assert_not_called()

    def test_el_guardia_de_charla_excluye_acciones_y_consultas(self):
        self.assertTrue(api_deisy._es_charla_final(["charla: hola"]))
        self.assertFalse(api_deisy._es_charla_final(["sistema: subir_volumen"]))
        self.assertFalse(api_deisy._es_charla_final(["radar:"]))
        self.assertFalse(api_deisy._es_charla_final(["charla: hola", "tecla: play"]))


if __name__ == "__main__":
    unittest.main()