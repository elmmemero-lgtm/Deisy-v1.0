import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import api_deisy
import cerebro


def _respuesta_modelo(contenido):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=contenido))]
    )


class CerebroResilienciaTest(unittest.TestCase):
    def setUp(self):
        cerebro._MEMORIAS_CHARLA.clear()

    def test_router_envia_politica_de_familias(self):
        with (
            patch.dict(os.environ, {"MODELO_ROUTER": "prueba"}, clear=False),
            patch.object(
                cerebro.client.chat.completions,
                "create",
                return_value=_respuesta_modelo("charla: hola"),
            ) as crear,
        ):
            cerebro.procesar_instruccion("abre Spotify", {"apps"})

        sistema = crear.call_args.kwargs["messages"][0]["content"]
        self.assertIn("POLÍTICA DE ENTRADA", sistema)
        self.assertIn("apps", sistema)

    def test_chat_vacio_reintenta_una_vez(self):
        with (
            patch.dict(os.environ, {"MODELO_CHARLA": "prueba"}, clear=False),
            patch.object(
                cerebro.client.chat.completions,
                "create",
                side_effect=[
                    _respuesta_modelo("<think>razonamiento</think>"),
                    _respuesta_modelo("Hola, Eddie."),
                ],
            ) as crear,
        ):
            ok, texto = cerebro.generar_respuesta_charla("hola", sesion="prueba:retry")

        self.assertTrue(ok)
        self.assertEqual(texto, "Hola, Eddie.")
        self.assertEqual(crear.call_count, 2)

    def test_chat_dos_respuestas_vacias_usa_fallback_sin_detalles(self):
        with (
            patch.dict(os.environ, {"MODELO_CHARLA": "prueba"}, clear=False),
            patch.object(
                cerebro.client.chat.completions,
                "create",
                side_effect=[_respuesta_modelo(""), _respuesta_modelo("<analysis>x</analysis>")],
            ) as crear,
        ):
            ok, texto = cerebro.generar_respuesta_charla("hola", sesion="prueba:fallback")

        self.assertFalse(ok)
        self.assertEqual(crear.call_count, 2)
        self.assertIn("respuesta fiable", texto)
        self.assertNotIn("TypeError", texto)
        self.assertNotIn("prueba", texto)

    def test_error_del_cliente_no_expone_excepcion(self):
        with (
            patch.dict(os.environ, {"MODELO_CHARLA": "prueba"}, clear=False),
            patch.object(
                cerebro.client.chat.completions,
                "create",
                side_effect=TypeError("interno"),
            ),
        ):
            ok, texto = cerebro.generar_respuesta_charla("hola", sesion="prueba:error")

        self.assertFalse(ok)
        self.assertNotIn("TypeError", texto)
        self.assertNotIn("interno", texto)


class ApiContextoMovilTest(unittest.TestCase):
    def setUp(self):
        api_deisy._ULTIMA_UBICACION.clear()
        api_deisy._ESTADOS_IPHONE.clear()
        api_deisy._EVENTOS_MOVIL_RECIENTES.clear()
        self.cliente = api_deisy.app.test_client()
        self.token = patch.object(api_deisy, "_requiere_token", return_value=None)
        self.token.start()
        self.addCleanup(self.token.stop)

    def test_clima_natural_y_direccion_rechazada(self):
        self.assertTrue(api_deisy._peticion_clima("el clima cómo está"))
        self.assertEqual(
            api_deisy._ciudad_contextual_segura("Calle del Salvador, 36"),
            "",
        )
        self.assertEqual(
            api_deisy._ciudad_contextual_segura("Parla, Madrid"),
            "Parla, Madrid",
        )
        self.assertEqual(
            api_deisy._ciudad_contextual_segura("Ubicación no indicada"),
            "",
        )

    def test_radar_duplicado_notifica_una_vez(self):
        carga = {
            "dispositivo": "iPhone 17",
            "latitud": "40.2371",
            "longitud": "-3.7648",
        }
        with patch.object(api_deisy.notificador, "notificar") as notificar:
            primera = self.cliente.post("/ubicacion_espia", json=carga)
            segunda = self.cliente.post("/ubicacion_espia", json=carga)

        self.assertEqual(primera.status_code, 200)
        self.assertFalse(primera.get_json()["duplicado"])
        self.assertTrue(segunda.get_json()["duplicado"])
        self.assertEqual(notificar.call_count, 1)

    def test_enfoque_normaliza_y_rechaza_valor_equivocado(self):
        with patch.object(api_deisy, "actualizar_contexto") as actualizar:
            valido = self.cliente.post(
                "/estado_iphone",
                json={"dispositivo": "iPhone 17", "enfoque": "flujo"},
            )
            invalido = self.cliente.post(
                "/estado_iphone",
                json={"dispositivo": "iPhone 17", "enfoque": "iPhone 11"},
            )

        self.assertEqual(valido.status_code, 200)
        self.assertEqual(valido.get_json()["enfoque"], "Flujo")
        self.assertEqual(invalido.status_code, 400)
        actualizar.assert_called_once_with("enfoque", "Flujo", "iPhone 17")

    def test_consulta_radar_natural_no_llama_al_router(self):
        api_deisy._ULTIMA_UBICACION["iphone 17"] = {
            "dispositivo": "iPhone 17",
            "link": "https://www.google.com/maps?q=40.0,-3.0",
            "lat": 40.0,
            "lon": -3.0,
            "cuando": api_deisy.datetime.now(),
        }
        with (
            patch.object(api_deisy, "procesar_instruccion") as router,
            patch.object(api_deisy, "guardar_historial"),
            patch.object(api_deisy, "_registrar_reflexion_no_bloqueante"),
        ):
            respuesta = self.cliente.post(
                "/preguntar",
                json={
                    "mensaje": "mándame la ubicación de mi iPhone",
                    "dispositivo": "telegram",
                },
            )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("google.com/maps", respuesta.get_json()["respuesta"])
        router.assert_not_called()


if __name__ == "__main__":
    unittest.main()