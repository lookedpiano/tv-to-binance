async function refresh(type) {
    const overlay = document.getElementById('overlay');
    const overlayText = document.getElementById('overlay-text');
    const spinner = document.querySelector('.spinner');
    overlayText.textContent = `Refreshing ${type}…`;
    overlayText.style.color = "#ff6600";
    spinner.style.display = 'block';
    overlay.style.display = 'flex';

    const params = new URLSearchParams(window.location.search);
    const key = params.get('key');
    let url = "";

    if (type === "balances") url = "/cache/refresh/balances";
    else if (type === "filters") url = "/cache/refresh/filters";
    else if (type === "prices") url = "/cache/prices";
    else {
        overlayText.textContent = "Unknown refresh type";
        spinner.style.display = 'none';
        setTimeout(() => overlay.style.display = 'none', 1500);
        return;
    }

    const opts = (type === "prices")
        ? { method: "GET" }
        : { method: "POST", headers: key ? { "X-Admin-Key": key } : {} };

    try {
        const resp = await fetch(url, opts);
        const text = await resp.text();
        let data;
        try { data = JSON.parse(text); } catch (e) { data = { raw: text }; }

        if (!resp.ok) {
            spinner.style.display = 'none';
            overlayText.textContent = (data && (data.error || data.message)) || `HTTP ${resp.status}`;
            overlayText.style.color = "#ff3333";
            setTimeout(() => overlay.style.display = 'none', 2500);
            return;
        }

        spinner.style.display = 'none';
        overlayText.textContent = `✓ ${type.charAt(0).toUpperCase() + type.slice(1)} refreshed successfully`;
        overlayText.style.color = "#00cc66";

        setTimeout(() => {
            overlay.style.display = 'none';
            location.reload();
        }, 1500);
    } catch (err) {
        spinner.style.display = 'none';
        overlayText.textContent = "Refresh failed: " + err;
        overlayText.style.color = "#ff3333";
        setTimeout(() => overlay.style.display = 'none', 2500);
    }
}