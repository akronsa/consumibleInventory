(function () {
  const API_ERROR_MESSAGES = {
    glpi_timeout: "GLPI no respondio a tiempo. Reintenta en unos segundos.",
    glpi_unavailable: "No se pudo conectar con GLPI. Reintenta en unos segundos.",
    glpi_auth_failed: "GLPI rechazo la autenticacion del backend. Revisar credenciales.",
    glpi_upstream_error: "GLPI devolvio un error interno. Reintenta en unos segundos.",
    glpi_request_rejected: "GLPI rechazo la solicitud.",
    glpi_invalid_response: "GLPI devolvio una respuesta invalida.",
  };

  function getApiError(payload, fallbackMessage) {
    const detail = payload && typeof payload === "object" ? payload.detail : undefined;
    if (detail && typeof detail === "object") {
      return {
        message: API_ERROR_MESSAGES[detail.code] || detail.error || fallbackMessage,
        code: detail.code || null,
        upstreamStatus: detail.upstream_status || null,
      };
    }

    if (typeof detail === "string" && detail.trim()) {
      return { message: detail.trim(), code: null, upstreamStatus: null };
    }

    return {
      message: fallbackMessage,
      code: null,
      upstreamStatus: null,
    };
  }

  async function readJsonResponse(response) {
    return response.json().catch(() => ({}));
  }

  window.getApiError = getApiError;
  window.readJsonResponse = readJsonResponse;
})();