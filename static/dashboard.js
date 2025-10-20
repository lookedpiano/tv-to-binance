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

async function fetchJson(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${url} returned ${resp.status}`);
    return await resp.json();
}

async function refreshSystemStatus() {
    const overlay = document.getElementById('overlay');
    const overlayText = document.getElementById('overlay-text');
    const spinner = document.querySelector('.spinner');
    overlayText.textContent = "Refreshing system status…";
    overlayText.style.color = "#ff6600";
    spinner.style.display = 'block';
    overlay.style.display = 'flex';

    try {
        const [health, summary] = await Promise.all([
            fetchJson('/health-check'),
            fetchJson('/cache/summary')
        ]);

        const content = `
            <p><b>Health:</b> ${health.status}</p>
            <h4>Cache Summary</h4>
            <ul>
                <li>Balances cached: ${summary.balances.exists}</li>
                <li>Filters: ${summary.filters.count}</li>
            </ul>
        `;

        document.getElementById('system-status-content').innerHTML = content;

        spinner.style.display = 'none';
        overlayText.textContent = "✓ System status updated";
        overlayText.style.color = "#00cc66";
        setTimeout(() => {
            overlay.style.display = 'none';
            sessionStorage.setItem('refreshStatusOnce', '1');  // mark reload trigger
            location.reload();
        }, 1500);
    } catch (err) {
        console.error(err);
        spinner.style.display = 'none';
        overlayText.textContent = "Failed to load system status: " + err.message;
        overlayText.style.color = "#ff3333";
        setTimeout(() => overlay.style.display = 'none', 2500);
    }
}

// Only auto-refresh system status ONCE after a reload triggered by “Refresh Status”
window.addEventListener('DOMContentLoaded', () => {
    if (sessionStorage.getItem('refreshStatusOnce')) {
        sessionStorage.removeItem('refreshStatusOnce');
        refreshSystemStatus();
    }
});
