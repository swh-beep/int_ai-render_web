(() => {
    const VERSION_URL = "/version.json";
    const CHECK_INTERVAL_MS = 60000;
    let currentVersion = null;
    let bannerShownFor = null;

    function showOverlay() {
        let overlay = document.getElementById("update-overlay");
        if (!overlay) {
            overlay = document.createElement("div");
            overlay.id = "update-overlay";
            overlay.className = "update-overlay hidden";
            overlay.innerHTML = `
                <div class="update-modal">
                    <div class="update-title">새 버전 배포됨</div>
                    <div class="update-text">작업 저장 후 창을 닫고 새 창에서 다시 작업하세요</div>
                    <button class="update-btn" type="button">확인</button>
                </div>
            `;
            document.body.appendChild(overlay);

            const btn = overlay.querySelector(".update-btn");
            if (btn) {
                btn.addEventListener("click", () => {
                    overlay.classList.add("hidden");
                });
            }
        }
        overlay.classList.remove("hidden");
    }

    async function checkVersion() {
        try {
            const res = await fetch(`${VERSION_URL}?t=${Date.now()}`, { cache: "no-store" });
            if (!res.ok) {
                return;
            }
            const data = await res.json();
            const serverVersion = String(data.version || "").trim();
            if (!serverVersion) {
                return;
            }
            if (!currentVersion) {
                currentVersion = serverVersion;
                return;
            }
            if (serverVersion !== currentVersion && bannerShownFor !== serverVersion) {
                bannerShownFor = serverVersion;
                showOverlay();
            }
        } catch (err) {
            // No-op: network or parsing error.
        }
    }

    const start = () => {
        checkVersion();
        setInterval(checkVersion, CHECK_INTERVAL_MS);
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();
