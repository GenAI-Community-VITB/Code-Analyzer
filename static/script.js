// --- THEME MANAGEMENT ---
function toggleTheme() {
    const html = document.documentElement;
    const current = html.getAttribute('data-theme');
    const target = current === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', target);
    localStorage.setItem('theme', target);
    updateChartColor(target);
}

const savedTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', savedTheme);

let scoreChart;
let lastAnalysis = null;
let trashThreshold = 50;

// --- CHART CONFIGURATION ---
document.addEventListener('DOMContentLoaded', function() {
    const ctx = document.getElementById('scoreChart').getContext('2d');

    scoreChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            datasets: [{
                data: [0, 100],
                backgroundColor: ['#e2e8f0', '#f1f5f9'],
                borderWidth: 0,
                cutout: '88%',
                borderRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { tooltip: { enabled: false } },
            animation: { animateScale: true, animateRotate: true }
        }
    });

    updateChartColor(savedTheme);
});

function updateChartColor(theme) {
    if (!scoreChart) return;
    const emptyColor = theme === 'dark' ? '#334155' : '#e2e8f0';
    scoreChart.data.datasets[0].backgroundColor[1] = emptyColor;
    scoreChart.update();
}

function setLoading(analyzeLoading, improveLoading) {
    const analyzeBtn = document.getElementById('analyzeBtn');
    const improveBtn = document.getElementById('improveBtn');
    const btnText = document.getElementById('btnText');
    const btnSpinner = document.getElementById('btnSpinner');
    const improveBtnText = document.getElementById('improveBtnText');
    const improveSpinner = document.getElementById('improveSpinner');

    analyzeBtn.disabled = analyzeLoading || improveLoading;
    improveBtn.disabled = analyzeLoading || improveLoading;

    btnText.style.display = analyzeLoading ? 'none' : 'block';
    btnSpinner.style.display = analyzeLoading ? 'block' : 'none';
    improveBtnText.style.display = improveLoading ? 'none' : 'block';
    improveSpinner.style.display = improveLoading ? 'block' : 'none';
}

function updateImproveButton() {
    const improveBtn = document.getElementById('improveBtn');
    if (!lastAnalysis) {
        improveBtn.title = 'Rewrite low-scoring prompts with Mistral';
        return;
    }

    const score = lastAnalysis.final_score || 0;
    const canImprove = score < trashThreshold || lastAnalysis.status === 'REJECTED';
    improveBtn.title = canImprove
        ? `Score below ${trashThreshold} — rewrite with Mistral`
        : `Score is above threshold (${trashThreshold}) — server will skip if not needed`;
}

async function analyzePrompt() {
    const input = document.getElementById('promptInput').value;
    const pill = document.getElementById('statusPill');

    if (!input.trim()) {
        alert('Please enter a prompt first.');
        return;
    }

    setLoading(true, false);
    pill.textContent = 'Processing...';
    pill.style.color = 'var(--text-secondary)';

    try {
        const response = await fetch('/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: input })
        });

        const data = await response.json();

        if (data.error) {
            pill.textContent = 'Error: ' + data.error;
            pill.style.color = 'var(--error-color)';
            lastAnalysis = null;
        } else {
            trashThreshold = data.threshold ?? trashThreshold;
            lastAnalysis = data;

            if (data.status === 'REJECTED') {
                updateResultUI(data.final_score, data.bert_score, 0, false);
                pill.textContent = data.msg || 'Status: Rejected (Low Quality)';
                pill.style.color = 'var(--error-color)';
            } else {
                updateResultUI(data.final_score, data.bert_score, data.llm_score, true);
                pill.textContent = 'Status: Accepted';
                pill.style.color = 'var(--success-color)';
            }
        }

        updateImproveButton();
    } catch (err) {
        console.error(err);
        pill.textContent = 'System Error';
        pill.style.color = 'var(--error-color)';
    } finally {
        setLoading(false, false);
        updateImproveButton();
    }
}

async function improvePrompt() {
    const input = document.getElementById('promptInput').value;
    const pill = document.getElementById('statusPill');

    if (!input.trim()) {
        alert('Please enter a prompt first.');
        return;
    }

    setLoading(false, true);
    pill.textContent = 'Improving prompt...';
    pill.style.color = 'var(--text-secondary)';

    try {
        const response = await fetch('/improve', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                prompt: input,
                known_score: lastAnalysis?.final_score ?? null,
                known_status: lastAnalysis?.status ?? null,
                known_bert: lastAnalysis?.bert_score ?? null,
                known_llm: lastAnalysis?.llm_score ?? null
            })
        });

        const data = await response.json();

        if (data.error) {
            pill.textContent = 'Error: ' + data.error;
            pill.style.color = 'var(--error-color)';
            return;
        }

        if (data.status === 'SKIPPED') {
            pill.textContent = data.msg || 'Already above threshold';
            pill.style.color = 'var(--warning-color)';
            return;
        }

        document.getElementById('promptInput').value = data.improved;

        const isSuccess = data.analysis_status === 'ACCEPTED';
        updateResultUI(
            data.final_score,
            data.bert_score,
            data.llm_score || 0,
            isSuccess
        );

        lastAnalysis = {
            final_score: data.final_score,
            bert_score: data.bert_score,
            llm_score: data.llm_score,
            status: data.analysis_status || 'ACCEPTED',
            threshold: data.threshold
        };

        if (data.status === 'IMPROVED') {
            pill.textContent = `Improved · ${data.iterations} iteration${data.iterations === 1 ? '' : 's'} · ${data.original_score?.toFixed(1)} → ${data.final_score?.toFixed(1)}`;
            pill.style.color = 'var(--success-color)';
        } else {
            pill.textContent = 'No improvement found';
            pill.style.color = 'var(--warning-color)';
        }

        updateImproveButton();
    } catch (err) {
        console.error(err);
        pill.textContent = 'System Error';
        pill.style.color = 'var(--error-color)';
    } finally {
        setLoading(false, false);
        updateImproveButton();
    }
}

function updateResultUI(final, bert, llm, isSuccess) {
    let color = '#ef4444';
    if (isSuccess) {
        if (final > 75) color = '#10b981';
        else if (final > 40) color = '#f59e0b';
    }

    const theme = document.documentElement.getAttribute('data-theme');
    const emptyColor = theme === 'dark' ? '#334155' : '#e2e8f0';

    scoreChart.data.datasets[0].backgroundColor = [color, emptyColor];
    scoreChart.data.datasets[0].data = [final, 100 - final];
    scoreChart.update();

    animateValue('finalScore', 0, Math.round(final), 800);
    document.getElementById('bertScore').textContent = bert.toFixed(1);
    document.getElementById('llmScore').textContent = llm ? llm.toFixed(1) : 'N/A';
}

function animateValue(id, start, end, duration) {
    if (start === end) return;
    const range = end - start;
    const stepTime = Math.abs(Math.floor(duration / range)) || 20;
    const obj = document.getElementById(id);
    let current = start;
    const timer = setInterval(function() {
        current += end > start ? 1 : -1;
        obj.textContent = current;
        if (current == end) clearInterval(timer);
    }, stepTime);
}
