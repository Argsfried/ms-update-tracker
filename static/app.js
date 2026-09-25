const detailsCache = {};
let selectedCronJob = null;
let globalDefaultQuery = "";

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

function openDetails(updateId, title, classification) {
    const titleElem = document.getElementById('modalTitle');
    const modalElem = document.getElementById('detailsModal');
    const bodyElem = document.getElementById('detailsBody');
    
    if (titleElem) titleElem.innerText = title;
    if (modalElem) modalElem.style.display = 'flex';

    if (detailsCache[updateId]) {
        if (bodyElem) bodyElem.innerHTML = detailsCache[updateId];
        return;
    }

    if (bodyElem) bodyElem.innerHTML = "<i>Loading requested details...</i>";

    fetch('/details/' + updateId + '?classification=' + encodeURIComponent(classification))
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                if (bodyElem) bodyElem.innerHTML = "<p style='color:red;'>Failed to load details.</p>";
                return;
            }
            let html = "";
            for (const [key, value] of Object.entries(data)) {
                html += '<div class="detail-row"><span class="detail-label">' + key + ':</span><div class="detail-val">' + value + '</div></div>';
            }
            const renderedContent = html || "No details available.";
            detailsCache[updateId] = renderedContent;
            if (bodyElem) bodyElem.innerHTML = renderedContent;
        });
}

function toggleMonthField() {
    const modeElem = document.getElementById('email_mode');
    const groupElem = document.getElementById('specific_month_group');
    const inputElem = document.getElementById('target_month');
    if (!modeElem || !groupElem) return;

    const isSpecific = (modeElem.value === 'specific_month');
    groupElem.style.display = isSpecific ? 'flex' : 'none';
    if (!isSpecific && inputElem) {
        inputElem.value = '';
    }
}

function selectCronRadio(cronId, mode, month, query) {
    selectedCronJob = { id: cronId, mode: mode, month: month, query: query };
    const btn = document.getElementById('btnRunCronNow');
    if (btn) btn.disabled = false;
}

function loadCrons() {
    selectedCronJob = null;
    const btn = document.getElementById('btnRunCronNow');
    if (btn) btn.disabled = true;

    fetch('/api/crons')
        .then(res => res.json())
        .then(jobs => {
            const tbody = document.getElementById('cronListTable');
            if (!tbody) return;
            tbody.innerHTML = '';
            if (!jobs || jobs.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No cron jobs configured.</td></tr>';
                return;
            }
            jobs.forEach(j => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td style="text-align:center;">
                        <input type="radio" name="cronSelect" value="${j.id}">
                    </td>
                    <td><code>${j.schedule}</code></td>
                    <td><span style="font-size:0.85em; background:#f0f0f0; padding:2px 6px; border-radius:3px;">${j.query}</span></td>
                    <td><strong>${j.mode_display}</strong> ${j.month ? '('+j.month+')' : ''}</td>
                    <td><button type="button" class="btn btn-danger delete-cron-btn">Delete</button></td>
                `;
                
                const radio = tr.querySelector('input[type="radio"]');
                if (radio) {
                    radio.addEventListener('click', () => selectCronRadio(j.id, j.mode, j.month, j.query));
                }
                
                const deleteBtn = tr.querySelector('.delete-cron-btn');
                if (deleteBtn) {
                    deleteBtn.addEventListener('click', () => deleteCron(j.id));
                }
                
                tbody.appendChild(tr);
            });
        });
}

function openSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) modal.style.display = 'flex';

    fetch('/api/settings')
        .then(res => res.json())
        .then(cfg => {
            if (cfg) {
                const recip = document.getElementById('smtp_recipient');
                const defQ = document.getElementById('default_search_query');
                const cronQ = document.getElementById('cron_query_input');
                
                if (recip) recip.value = cfg.recipients || '';
                if (defQ) defQ.value = cfg.search_query || '';
                globalDefaultQuery = cfg.search_query || '';
                if (cronQ) cronQ.value = globalDefaultQuery;
            }
        });

    loadCrons();
}

function addCronLine() {
    const schedElem = document.getElementById('cron_schedule_input');
    const modeElem = document.getElementById('email_mode');
    const monthElem = document.getElementById('target_month');
    const queryElem = document.getElementById('cron_query_input');

    const schedule = schedElem ? schedElem.value : '';
    const mode = modeElem ? modeElem.value : 'each_new';
    const month = (mode === 'specific_month' && monthElem) ? monthElem.value : '';
    const query = (queryElem && queryElem.value) ? queryElem.value : globalDefaultQuery;

    if (!schedule) return showToast("Please enter a valid cron schedule like: 0 8 * * *", "error");

    fetch('/api/crons', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ schedule, mode, month, query })
    })
    .then(res => res.json())
    .then(res => {
        if (res.error) {
            showToast("Error adding cron: " + res.error, "error");
        } else {
            showToast("Cron schedule added successfully!", "success");
            if (schedElem) schedElem.value = '';
            if (monthElem) monthElem.value = '';
            loadCrons();
        }
    })
    .catch(err => showToast("Server error: " + err, "error"));
}

function deleteCron(id) {
    fetch('/api/crons/' + id, { method: 'DELETE' })
        .then(() => {
            showToast("Cron job removed.", "info");
            loadCrons();
        });
}

function saveSettings(e) {
    e.preventDefault();
    const recipElem = document.getElementById('smtp_recipient');
    const qElem = document.getElementById('default_search_query');

    const payload = {
        recipients: recipElem ? recipElem.value : '',
        search_query: qElem ? qElem.value : ''
    };

    fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(res => {
        showToast(res.message, "success");
        closeModal('settingsModal');
    });
}

function testEmail() {
    showToast("Sending test email...", "info");
    fetch('/api/test-email', { method: 'POST' })
        .then(res => res.json())
        .then(res => {
            if(res.status === 'success') {
                showToast(res.message, "success");
            } else {
                showToast(res.message, "error");
            }
        });
}

function triggerManualCron() {
    if (!selectedCronJob) {
        showToast("Please select a cronjob entry from the table first!", "error");
        return;
    }

    showToast("Executing background cron check...", "info");
    fetch(`/cron/check-updates?mode=${selectedCronJob.mode}&month=${selectedCronJob.month}&query=${selectedCronJob.query}`)
        .then(res => res.json())
        .then(data => showToast(`Cron executed successfully! Emailed ${data.found_new} update(s).`, "success"));
}

function closeModal(id) {
    const elem = document.getElementById(id);
    if (elem) elem.style.display = 'none';
}

// Bind event listeners upon page load
document.addEventListener('DOMContentLoaded', () => {
    const btnSettings = document.getElementById('btnOpenSettings');
    if (btnSettings) btnSettings.addEventListener('click', openSettingsModal);

    const closeBtnDetails = document.getElementById('closeDetailsBtn');
    if (closeBtnDetails) closeBtnDetails.addEventListener('click', () => closeModal('detailsModal'));

    const closeBtnSettings = document.getElementById('closeSettingsBtn');
    if (closeBtnSettings) closeBtnSettings.addEventListener('click', () => closeModal('settingsModal'));

    const emailModeSelect = document.getElementById('email_mode');
    if (emailModeSelect) emailModeSelect.addEventListener('change', toggleMonthField);

    const btnAddCron = document.getElementById('btnAddCron');
    if (btnAddCron) btnAddCron.addEventListener('click', addCronLine);

    const btnTestEmail = document.getElementById('btnTestEmail');
    if (btnTestEmail) btnTestEmail.addEventListener('click', testEmail);

    const btnRunCronNow = document.getElementById('btnRunCronNow');
    if (btnRunCronNow) btnRunCronNow.addEventListener('click', triggerManualCron);

    const settingsForm = document.getElementById('settingsForm');
    if (settingsForm) settingsForm.addEventListener('submit', saveSettings);

    document.querySelectorAll('.title-link').forEach(link => {
        link.addEventListener('click', (e) => {
            const uid = e.target.getAttribute('data-uid');
            const title = e.target.getAttribute('data-title');
            const classification = e.target.getAttribute('data-classification');
            openDetails(uid, title, classification);
        });
    });
});
