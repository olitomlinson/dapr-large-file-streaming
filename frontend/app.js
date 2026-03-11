document.getElementById('download-button').addEventListener('click', async () => {
    const sizeInput = document.getElementById('file-size');
    const filenameInput = document.getElementById('filename');
    const statusDiv = document.getElementById('status');
    const progressFill = document.getElementById('progress-fill');

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

    statusDiv.textContent = `Starting download of ${sizeMb}MB file...`;
    statusDiv.style.color = '#333';
    progressFill.style.width = '0%';

    const startTime = Date.now();

    try {
        // Trigger download using anchor tag approach
        // API gateway is on port 80
        const url = `http://localhost/download/large-file?size_mb=${sizeMb}&filename=${encodeURIComponent(filename)}`;
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        // Simulate progress (browser download happens in background)
        progressFill.style.width = '100%';
        statusDiv.textContent = `Download initiated! Check your browser downloads for "${filename}".`;

        const duration = ((Date.now() - startTime) / 1000).toFixed(2);

        // Add timing info
        setTimeout(() => {
            statusDiv.textContent += ` Request completed in ${duration}s.`;
        }, 100);

    } catch (error) {
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
