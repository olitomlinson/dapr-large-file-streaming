let monitoringInterval = null;
let baselineMemory = null;
let memoryHistory = [];

async function getMemoryStats() {
    try {
        const response = await fetch('http://localhost:8003/memory-stats');
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Error fetching memory stats:', error);
        return null;
    }
}

function updateMemoryDisplay(current, baseline) {
    const memoryDiv = document.getElementById('memory-stats');
    if (!current || !current.success) {
        memoryDiv.innerHTML = '<p style="color: red;">Memory monitoring unavailable</p>';
        return;
    }

    const mem = current.memory;
    const increases = {};
    let html = '<h4>Real-time Memory Usage:</h4><table style="width: 100%; font-size: 12px;">';
    html += '<tr><th>Container</th><th>Current</th><th>Baseline</th><th>Increase</th><th>% of Payload</th></tr>';

    const containers = ['nginx', 'nginx_dapr', 'file_generator', 'file_generator_dapr'];
    const labels = ['nginx', 'nginx-dapr', 'file-generator', 'file-generator-dapr'];
    const sizeMb = parseInt(document.getElementById('file-size').value);

    containers.forEach((key, index) => {
        const current_val = mem[key] || 0;
        const baseline_val = baseline?.memory?.[key] || current_val;
        const increase = current_val - baseline_val;
        const percent = sizeMb > 0 ? ((increase / sizeMb) * 100).toFixed(2) : 0;
        increases[key] = increase;

        const color = increase > sizeMb * 2 ? 'red' : increase > sizeMb * 0.5 ? 'orange' : 'green';
        html += `<tr>
            <td>${labels[index]}</td>
            <td>${current_val.toFixed(2)} MiB</td>
            <td>${baseline_val.toFixed(2)} MiB</td>
            <td style="color: ${color};">${increase.toFixed(2)} MiB</td>
            <td style="color: ${color};">${percent}%</td>
        </tr>`;
    });

    const totalIncrease = Object.values(increases).reduce((a, b) => a + b, 0);
    const totalPercent = sizeMb > 0 ? ((totalIncrease / sizeMb) * 100).toFixed(2) : 0;
    const totalColor = totalIncrease > sizeMb * 2 ? 'red' : totalIncrease > sizeMb * 0.5 ? 'orange' : 'green';

    html += `<tr style="font-weight: bold; border-top: 2px solid #333;">
        <td>Total Chain</td>
        <td>${current.total.toFixed(2)} MiB</td>
        <td>${baseline ? baseline.total.toFixed(2) : current.total.toFixed(2)} MiB</td>
        <td style="color: ${totalColor};">${totalIncrease.toFixed(2)} MiB</td>
        <td style="color: ${totalColor};">${totalPercent}%</td>
    </tr>`;
    html += '</table>';

    memoryDiv.innerHTML = html;
}

document.getElementById('download-button').addEventListener('click', async () => {
    const sizeInput = document.getElementById('file-size');
    const filenameInput = document.getElementById('filename');
    const statusDiv = document.getElementById('status');
    const progressFill = document.getElementById('progress-fill');
    const memoryDiv = document.getElementById('memory-stats');

    const sizeMb = parseInt(sizeInput.value);
    const filename = filenameInput.value;

    if (!sizeMb || sizeMb < 1 || sizeMb > 1000) {
        statusDiv.textContent = 'Please enter a valid file size between 1 and 1000 MB';
        statusDiv.style.color = 'red';
        return;
    }

    if (!filename || filename.trim() === '') {
        statusDiv.textContent = 'Please enter a valid filename';
        statusDiv.style.color = 'red';
        return;
    }

    // Clear previous monitoring
    if (monitoringInterval) {
        clearInterval(monitoringInterval);
        monitoringInterval = null;
    }
    memoryHistory = [];

    statusDiv.textContent = `Capturing baseline memory...`;
    statusDiv.style.color = '#333';
    progressFill.style.width = '0%';

    // Get baseline memory
    baselineMemory = await getMemoryStats();
    if (baselineMemory) {
        updateMemoryDisplay(baselineMemory, baselineMemory);
    }

    await new Promise(resolve => setTimeout(resolve, 1000));

    statusDiv.textContent = `Starting download of ${sizeMb}MB file...`;
    const startTime = Date.now();

    // Start memory monitoring
    monitoringInterval = setInterval(async () => {
        const stats = await getMemoryStats();
        if (stats) {
            memoryHistory.push({ timestamp: Date.now(), stats });
            updateMemoryDisplay(stats, baselineMemory);
        }
    }, 500); // Poll every 500ms

    try {
        // Trigger download using anchor tag approach
        const url = `http://localhost/download/large-file?size_mb=${sizeMb}&filename=${encodeURIComponent(filename)}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        progressFill.style.width = '100%';
        statusDiv.textContent = `Download initiated! Monitoring memory...`;

        const duration = ((Date.now() - startTime) / 1000).toFixed(2);

        // Continue monitoring for a few more seconds after download starts
        setTimeout(() => {
            if (monitoringInterval) {
                clearInterval(monitoringInterval);
                monitoringInterval = null;
            }
            statusDiv.textContent = `Download complete! Duration: ${duration}s. Memory stats captured.`;
        }, 5000);

    } catch (error) {
        if (monitoringInterval) {
            clearInterval(monitoringInterval);
            monitoringInterval = null;
        }
        statusDiv.textContent = `Error: ${error.message}`;
        statusDiv.style.color = 'red';
        progressFill.style.width = '0%';
    }
});

// Allow Enter key to trigger download
document.getElementById('file-size').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        document.getElementById('download-button').click();
    }
});

document.getElementById('filename').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        document.getElementById('download-button').click();
    }
});
