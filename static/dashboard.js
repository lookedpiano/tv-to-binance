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
    else if (type === "orders") url = "/cache/orders";
    else {
        overlayText.textContent = "Unknown refresh type";
        spinner.style.display = 'none';
        setTimeout(() => overlay.style.display = 'none', 1500);
        return;
    }

    const opts = (type === "prices")
        ? { method: "GET" }
        : { 
            method: "GET",  // orders also uses GET
            headers: key ? { "X-Admin-Key": key } : {}
          };

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

        if (type === "orders") {
            renderOrders(data);
        } else {
            setTimeout(() => {
                overlay.style.display = 'none';
                location.reload();
            }, 1500);
            return;
        }

        setTimeout(() => overlay.style.display = 'none', 1500);
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
        const params = new URLSearchParams(window.location.search);
        const key = params.get('key');
        const headers = key ? { "X-Admin-Key": key } : {};

        const [health] = await Promise.all([
            fetchJsonWithHeaders('/health-check', headers)
        ]);

        const isHealthy = health.status?.toLowerCase() === 'healthy';
        const color = isHealthy ? '#00cc66' : '#ff3333';
        const content = `
            <p><b>Health:</b> <span style="color:${color};">${health.status}</span></p>
        `;

        document.getElementById('system-status-content').innerHTML = content;
        sessionStorage.setItem('systemStatusHTML', content);

        spinner.style.display = 'none';
        overlayText.textContent = "✓ System status updated";
        overlayText.style.color = "#00cc66";

        setTimeout(() => {
            overlay.style.display = 'none';
            location.reload();
        }, 1500);
    } catch (err) {
        console.error(err);
        spinner.style.display = 'none';
        overlayText.textContent = "Failed to load system status: " + err.message;
        overlayText.style.color = "#ff3333";
        setTimeout(() => overlay.style.display = 'none', 2500);

        document.getElementById('system-status-content').innerHTML = `
            <p><b>Health:</b> <span style="color:#ff3333;">unhealthy</span></p>
        `;
    }
}

async function fetchJsonWithHeaders(url, headers = {}) {
    const resp = await fetch(url, { headers });
    if (!resp.ok) throw new Error(`${url} returned ${resp.status}`);
    return await resp.json();
}

function renderOrders(data) {
    if (!data || !data.orders) return;

    const table = document.getElementById('orders-table');
    const count = document.getElementById('orders-count');
    const last = document.getElementById('orders-last');

    // Reset table with header
    table.innerHTML = `
        <tr>
            <th>Timestamp</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Price</th>
            <th>Quantity</th>
            <th>Status</th>
            <th>Message</th>
        </tr>
    `;

    data.orders.forEach(order => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${formatTimestamp(order.time || order.timestamp)}</td>
            <td>${order.symbol || '-'}</td>
            <td>${order.side || '-'}</td>
            <td>${order.price ?? '-'}</td>
            <td>${order.qty ?? order.quantity ?? '-'}</td>
            <td>${order.status || '-'}</td>
            <td>${order.message || '-'}</td>
        `;
        table.appendChild(tr);
    });

    count.textContent = data.count ?? data.orders.length;
    last.textContent = new Date().toLocaleString();
}

function formatTimestamp(ts) {
    if (!ts) return '-';
    const date = new Date(Number(ts));
    return isNaN(date.getTime()) ? ts : date.toLocaleString();
}

// Restore cached System Status after any reload
window.addEventListener('DOMContentLoaded', () => {
    const saved = sessionStorage.getItem('systemStatusHTML');
    if (saved) {
        document.getElementById('system-status-content').innerHTML = saved;
    }
});
