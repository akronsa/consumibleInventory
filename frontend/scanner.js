(function () {
  function initBarcodeScanner(options) {
    const settings = options || {};
    const getById = settings.getById || ((id) => document.getElementById(id));
    const inputId = settings.inputId;
    const onDetected = settings.onDetected;
    const storageKey = settings.storageKey || "scanner_camera_id";
    const cropWidth = settings.cropWidth || 0.65;
    const cropHeight = settings.cropHeight || 0.55;
    const idleText = settings.idleText || "Apuntá la cámara al código...";
    const unsupportedText = settings.unsupportedText || "Se requiere Chrome actualizado para usar la cámara.";
    const deniedText = settings.deniedText || "Permiso de cámara denegado.";
    const noCameraText = settings.noCameraText || "No se encontró ninguna cámara.";
    const unavailableText = settings.unavailableText || "No se pudo acceder a la cámara.";

    const modal = getById("scannerModal");
    const status = getById("scannerStatus");
    const torchButton = getById("btnTorch");
    const switchCameraButton = getById("btnSwitchCam");
    const cameraLabel = getById("camLabel");
    const video = getById("scannerVideo");
    const crosshair = getById("scannerCrosshair");
    const focusRing = getById("focusRing");
    const openButton = getById("btnCamera");
    const closeButton = getById("btnScannerClose");

    if (!inputId || !modal || !status || !torchButton || !switchCameraButton || !cameraLabel || !video || !crosshair || !focusRing || !openButton || !closeButton) {
      throw new Error("Scanner configuration is incomplete");
    }

    let mediaStream = null;
    let scanLoopId = null;
    let refocusIntervalId = null;
    let barcodeDetector = null;
    let videoTrack = null;
    let torchOn = false;
    let cameras = [];
    let camIndex = 0;

    function setStatus(text, isError) {
      status.className = isError ? "scanner-status err" : "scanner-status";
      status.textContent = text;
    }

    function emitCode(code) {
      if (typeof onDetected === "function") {
        onDetected(code);
        return;
      }

      const input = getById(inputId);
      if (!input) return;
      input.value = code;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function stopScanner() {
      cancelAnimationFrame(scanLoopId);
      clearInterval(refocusIntervalId);
      scanLoopId = null;
      refocusIntervalId = null;
      barcodeDetector = null;
      cameras = [];
      camIndex = 0;
      if (videoTrack && torchOn) {
        videoTrack.applyConstraints({ advanced: [{ torch: false }] }).catch(() => {});
      }
      if (mediaStream) {
        mediaStream.getTracks().forEach((track) => track.stop());
        mediaStream = null;
      }
      video.srcObject = null;
      videoTrack = null;
      torchOn = false;
    }

    function closeScanner() {
      stopScanner();
      modal.classList.remove("visible");
    }

    async function applyFocus(x, y) {
      if (!videoTrack) return;
      const caps = videoTrack.getCapabilities?.() || {};
      if (!caps.focusMode) return;
      try {
        crosshair.classList.add("focusing");
        const constraints = { advanced: [{ focusMode: "single-shot" }] };
        if (caps.pointsOfInterest) {
          constraints.advanced[0].pointsOfInterest = [{ x: x ?? 0.5, y: y ?? 0.5 }];
        }
        await videoTrack.applyConstraints(constraints);
      } catch (_) {
      } finally {
        setTimeout(() => crosshair.classList.remove("focusing"), 600);
      }
    }

    function scanLoop(cropCanvas) {
      const ctx = cropCanvas.getContext("2d");

      const tick = async () => {
        if (!barcodeDetector || !mediaStream) return;
        try {
          const videoWidth = video.videoWidth;
          const videoHeight = video.videoHeight;
          if (videoWidth && videoHeight) {
            const sourceWidth = videoWidth * cropWidth;
            const sourceHeight = videoHeight * cropHeight;
            const sourceX = (videoWidth - sourceWidth) / 2;
            const sourceY = (videoHeight - sourceHeight) / 2;
            ctx.drawImage(video, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, 1024, 1024);
            const codes = await barcodeDetector.detect(cropCanvas);
            if (codes.length > 0) {
              const code = codes[0].rawValue;
              closeScanner();
              emitCode(code);
              return;
            }
          }
        } catch (_) {
        }
        scanLoopId = requestAnimationFrame(tick);
      };

      scanLoopId = requestAnimationFrame(tick);
    }

    async function startScanner(index) {
      if (mediaStream) {
        mediaStream.getTracks().forEach((track) => track.stop());
        mediaStream = null;
      }
      cancelAnimationFrame(scanLoopId);
      clearInterval(refocusIntervalId);

      if (!("BarcodeDetector" in window)) {
        setStatus(unsupportedText, true);
        return;
      }

      try {
        if (cameras.length === 0) {
          const tempStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } } });
          tempStream.getTracks().forEach((track) => track.stop());

          const allDevices = await navigator.mediaDevices.enumerateDevices();
          const allVideoDevices = allDevices.filter((device) => device.kind === "videoinput");
          cameras = allVideoDevices.filter((device) => !device.label.toLowerCase().includes("front") && !device.label.toLowerCase().includes("facing front"));
          if (cameras.length === 0) {
            cameras = allVideoDevices;
          }

          if (index == null) {
            const savedId = localStorage.getItem(storageKey);
            const savedIndex = savedId ? cameras.findIndex((camera) => camera.deviceId === savedId) : -1;
            camIndex = savedIndex >= 0 ? savedIndex : 0;
          } else {
            camIndex = index;
          }
        } else {
          camIndex = index ?? camIndex;
        }

        const camera = cameras[camIndex];
        mediaStream = await navigator.mediaDevices.getUserMedia({
          video: {
            deviceId: { exact: camera.deviceId },
            width: { ideal: 3840 },
            height: { ideal: 2160 },
          },
        });

        video.srcObject = mediaStream;
        await video.play();

        videoTrack = mediaStream.getVideoTracks()[0];
        const caps = videoTrack.getCapabilities?.() || {};

        localStorage.setItem(storageKey, camera.deviceId);
        const label = camera.label || `Cámara ${camIndex + 1}`;
        cameraLabel.textContent = `${camIndex + 1} / ${cameras.length} — ${label}`;

        if (cameras.length > 1) {
          switchCameraButton.classList.add("visible");
        }
        if (caps.torch) {
          torchButton.classList.add("visible");
        } else {
          torchButton.classList.remove("visible", "on");
        }
        torchOn = false;

        await applyFocus(0.5, 0.5);
        refocusIntervalId = setInterval(() => applyFocus(0.5, 0.5), 2500);

        const cropCanvas = document.createElement("canvas");
        cropCanvas.width = 1024;
        cropCanvas.height = 1024;

        if (!barcodeDetector) {
          const formats = await BarcodeDetector.getSupportedFormats();
          barcodeDetector = new BarcodeDetector({ formats });
        }
        scanLoop(cropCanvas);
      } catch (error) {
        if (error.name === "NotAllowedError") {
          setStatus(deniedText, true);
        } else if (error.name === "NotFoundError") {
          setStatus(noCameraText, true);
        } else {
          setStatus(unavailableText, true);
        }
        stopScanner();
      }
    }

    function openScanner() {
      modal.classList.add("visible");
      setStatus(idleText, false);
      torchButton.classList.remove("visible", "on");
      switchCameraButton.classList.remove("visible");
      cameraLabel.textContent = "";
      torchOn = false;
      startScanner();
    }

    video.addEventListener("click", (event) => {
      if (!videoTrack) return;
      const rect = event.target.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width;
      const y = (event.clientY - rect.top) / rect.height;
      focusRing.style.left = `${event.clientX - rect.left}px`;
      focusRing.style.top = `${event.clientY - rect.top}px`;
      focusRing.classList.add("active");
      setTimeout(() => focusRing.classList.remove("active"), 700);
      applyFocus(x, y);
    });

    switchCameraButton.addEventListener("click", () => {
      if (cameras.length < 2) return;
      startScanner((camIndex + 1) % cameras.length);
    });

    torchButton.addEventListener("click", async () => {
      if (!videoTrack) return;
      try {
        torchOn = !torchOn;
        await videoTrack.applyConstraints({ advanced: [{ torch: torchOn }] });
        torchButton.classList.toggle("on", torchOn);
      } catch (_) {
        torchOn = !torchOn;
      }
    });

    openButton.addEventListener("click", openScanner);
    closeButton.addEventListener("click", closeScanner);
    modal.addEventListener("click", (event) => {
      if (event.target === modal) closeScanner();
    });

    return { openScanner, closeScanner };
  }

  window.initBarcodeScanner = initBarcodeScanner;
})();