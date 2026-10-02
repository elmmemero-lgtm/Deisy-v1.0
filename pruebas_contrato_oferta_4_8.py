import os
import unittest
from unittest.mock import Mock, patch

import api_deisy
from politica_entrada import cancelar_oferta_basica, registrar_oferta_basica


class OfertaBasicaApiTest(unittest.TestCase):
    def setUp(self):
        self.sesion = f"prueba-api-oferta-4.8-{self._testMethodName}"
        cancelar_oferta_basica(self.sesion)
        self.cliente = api_deisy.app.test_client()

    def _post(self, mensaje):
        return self.cliente.post(
            "/preguntar",
            json={
                "mensaje": mensaje,
                "dispositivo": "prueba",
                "sesion": self.sesion,
            },
        )

    def _parches_neutros(self, ejecutor):
        """Evita base de datos/red; el test solo mide el flujo de API."""
        return (
            patch.object(api_deisy, "_requiere_token", return_value=None),
            patch.object(api_deisy, "guardar_historial"),
            patch.object(api_deisy, "registrar_turno_chat"),
            patch.object(api_deisy, "resolver_contexto_corto", return_value=("", "")),
            patch.object(api_deisy, "resolver_intencion_local", return_value=None),
            patch.object(api_deisy, "recuperar_memoria_para_charla", return_value=""),
            patch.object(api_deisy, "_ejecutar_comando", ejecutor),
        )

    def test_oferta_visible_y_si_ejecutan_una_sola_orden(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        ejecutor = Mock(return_value=("Spotify abierto.", True, "musica"))
        with patch.dict(os.environ, {"DEISY_OFERTAS_BASICAS_ACTIVAS": "true"}, clear=False):
            parches = self._parches_neutros(ejecutor)
            with parches[0], parches[1], parches[2], parches[3], parches[4], parches[5], parches[6]:
                respuesta = self._post("Sí, por favor")

        self.assertEqual(respuesta.status_code, 200)
        ejecutor.assert_called_once()

    def test_si_sin_oferta_no_llega_al_ejecutor(self):
        ejecutor = Mock(return_value=("No debería ejecutarse.", True, "musica"))
        with patch.dict(os.environ, {"DEISY_OFERTAS_BASICAS_ACTIVAS": "true"}, clear=False), \
             patch.object(api_deisy, "_requiere_token", return_value=None), \
             patch.object(api_deisy, "guardar_historial"), \
             patch.object(api_deisy, "registrar_turno_chat"), \
             patch.object(api_deisy, "recuperar_memoria_para_charla", return_value=""), \
             patch.object(api_deisy, "generar_respuesta_charla", return_value=(True, "Charla normal.")), \
             patch.object(api_deisy, "_ejecutar_comando", ejecutor):
            respuesta = self._post("sí")

        self.assertEqual(respuesta.status_code, 200)
        ejecutor.assert_not_called()

    def test_consulta_local_cancela_la_oferta(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        ejecutor = Mock(return_value=("No debería ejecutarse.", True, "musica"))
        with patch.dict(os.environ, {"DEISY_OFERTAS_BASICAS_ACTIVAS": "true"}, clear=False), \
             patch.object(api_deisy, "_requiere_token", return_value=None), \
             patch.object(api_deisy, "guardar_historial"), \
             patch.object(api_deisy, "registrar_turno_chat"), \
             patch.object(api_deisy, "recuperar_memoria_para_charla", return_value=""), \
             patch.object(api_deisy, "generar_respuesta_charla", return_value=(True, "Charla normal.")), \
             patch.object(api_deisy, "_ejecutar_comando", ejecutor):
            self._post("dime la hora")
            self._post("sí")

        ejecutor.assert_not_called()

    def test_si_y_apagado_no_acepta_spotify(self):
        registrar_oferta_basica(self.sesion, "abrir_spotify_omen")
        ejecutor = Mock(return_value=("No debería ejecutarse.", True, "musica"))
        decision_apagado = {
            "tipo": "respuesta",
            "texto": "Confirma de forma descriptiva el apagado de la Omen.",
            "modulo": "confirmacion_apagado",
        }
        with patch.dict(os.environ, {"DEISY_OFERTAS_BASICAS_ACTIVAS": "true"}, clear=False), \
             patch.object(api_deisy, "_requiere_token", return_value=None), \
             patch.object(api_deisy, "guardar_historial"), \
             patch.object(api_deisy, "resolver_intencion_local", return_value=decision_apagado), \
             patch.object(api_deisy, "_ejecutar_comando", ejecutor):
            respuesta = self._post("sí, y apaga la Omen")

        self.assertIn("Confirma", respuesta.get_json()["respuesta"])
        ejecutor.assert_not_called()