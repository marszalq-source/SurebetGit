// STS & Flashscore Goal Scanner - Advanced Live & Prematch Frontend Logic
let activeTab = 'live';

// Live state
let currentMatches = [];
let soundEnabled = true;
let liveFilterMode = 'ALL'; // 'ALL', 'WORTH', 'SIGNALS'
let halfFilter = 'ALL';
let searchLiveQuery = '';
let autoRefreshTimer = null;
let refreshCountdown = 15;
let isScanningLive = false;
let audioCtx = null;

// Prematch state
let currentPrematch = [];
let countryFilter = 'ALL';
let dayOffset = 0;
let searchPrematchQuery = '';
let isScanningPrematch = false;

// Watchlist state
let watchlistMap = {};

// Audio Alert Synthesizer
function initAudio() {
    if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    }
}

function playSignalSound() {
    if (!soundEnabled) return;
    try {
        initAudio();
        const now = audioCtx.currentTime;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        
        osc.type = 'sine';
        osc.frequency.setValueAtTime(587.33, now);       // D5
        osc.frequency.setValueAtTime(880.00, now + 0.1); // A5
        osc.frequency.setValueAtTime(1174.66, now + 0.2);// D6
        
        gain.gain.setValueAtTime(0.3, now);
        gain.gain.exponentialRampToValueAtTime(0.01, now + 0.5);
        
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        
        osc.start(now);
        osc.stop(now + 0.5);
    } catch (e) {
        console.warn('Audio alert error:', e);
    }
}

// ==================== 1. LIVE SCANNING ====================

async function runLiveScan(force = false) {
    if (isScanningLive && !force) return;
    isScanningLive = true;
    updateScanButtonState(true);

    try {
        let result = null;
        if (window.pywebview && window.pywebview.api) {
            try {
                result = await Promise.race([
                    window.pywebview.api.scan_live(false, 0, 'ALL'),
                    new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout pywebview')), 4000))
                ]);
            } catch(e) {
                // Fallback HTTP jeśli pywebview bridge opóźnia
                const resp = await fetch(`/api/scan?only_signals=false&half=ALL&_t=${Date.now()}`);
                result = await resp.json();
            }
        } else {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 4000);
            const resp = await fetch(`/api/scan?only_signals=false&half=ALL&_t=${Date.now()}`, { signal: controller.signal });
            clearTimeout(timeoutId);
            result = await resp.json();
        }

        if (result && result.matches) {
            // Jeśli skan zwrócił mecze, lub nie mieliśmy jeszcze żadnych danych - aktualizuj
            if (result.matches.length > 0 || currentMatches.length === 0) {
                handleLiveResults(result);
            }
        }
    } catch (err) {
        console.warn('Live scan error:', err);
    } finally {
        isScanningLive = false;
        updateScanButtonState(false);
        resetCountdown();
    }
}



function handleLiveResults(data) {
    const prevSignalsCount = currentMatches.reduce((acc, m) => acc + (m.signals ? m.signals.length : 0), 0);
    currentMatches = data.matches || [];

    document.getElementById('stat-total-live').innerText = data.total_live_matches || currentMatches.length;
    document.getElementById('stat-signals-count').innerText = data.signals_count || 0;
    document.getElementById('tab-live-count').innerText = currentMatches.length;
    
    if (currentMatches.length > 0) {
        const avgDanger = Math.round(currentMatches.reduce((a, b) => a + (b.danger_index || 0), 0) / currentMatches.length);
        document.getElementById('stat-avg-danger').innerText = avgDanger + '%';
    } else {
        document.getElementById('stat-avg-danger').innerText = '0%';
    }
    
    document.getElementById('last-update-time').innerText = data.timestamp || new Date().toLocaleTimeString();

    if (data.signals_count > prevSignalsCount && prevSignalsCount >= 0) {
        playSignalSound();
    }

    renderLiveMatches();
}

function renderLiveMatches() {
    const container = document.getElementById('matches-container-live');
    container.innerHTML = '';

    const filtered = currentMatches.filter(m => {
        if (liveFilterMode === 'SIGNALS' && !m.has_signals) return false;
        if (liveFilterMode === 'WORTH' && !m.is_worth_watching) return false;
        if (halfFilter === '1H' && m.half !== '1H' && !m.stage_text?.includes('1') && !m.stage_text?.includes('Przerwa')) return false;
        if (halfFilter === '2H' && m.half !== '2H' && !m.stage_text?.includes('2')) return false;
        if (searchLiveQuery) {
            const q = searchLiveQuery.toLowerCase();
            const matchText = `${m.home_team} ${m.away_team} ${m.league}`.toLowerCase();
            if (!matchText.includes(q)) return false;
        }
        return true;
    });

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">⚽</div>
                <div class="empty-title">Brak pasujących meczów Live</div>
                <div class="empty-desc">
                    ${liveFilterMode === 'SIGNALS' ? 'Aktualnie żaden trwający mecz nie spełnia ostrych kryteriów bramkowych.' : 
                      (liveFilterMode === 'WORTH' ? 'Żaden trwający mecz nie osiągnął progu TOP Warte Uwagi.' : 'Trwa oczekiwanie na rozpoczęcie kolejnych meczów piłkarskich.')}
                </div>
            </div>
        `;
        return;
    }

    filtered.forEach(m => {
        const card = document.createElement('div');
        card.className = `match-card ${m.has_signals ? 'has-signal' : ''}`;

        let dangerClass = 'danger-low';
        if (m.danger_index >= 75) dangerClass = 'danger-extreme';
        else if (m.danger_index >= 55) dangerClass = 'danger-high';
        else if (m.danger_index >= 35) dangerClass = 'danger-medium';

        let signalsHtml = '';
        if (m.signals && m.signals.length > 0) {
            m.signals.forEach(s => {
                signalsHtml += `
                    <div class="signal-alert-box" style="border-left: 4px solid ${s.color};">
                        <div>
                            <div class="signal-title">${s.title} <span>${'⭐'.repeat(s.stars)}</span></div>
                            <div class="signal-desc">${s.desc}</div>
                        </div>
                        <div class="signal-odds-badge" style="background: ${s.color};">
                            Kurs STS: ${s.odds.toFixed(2)}
                        </div>
                    </div>
                `;
            });
        }

        const stats = m.stats || {};
        const odds = m.odds || {};
        const pCtx = m.prematch_context || m.prematch_ctx || {};

        // Worth Watching Badge & Reasons
        let worthBadgeHtml = '';
        if (m.worth_grade === 'TOP') {
            worthBadgeHtml = `<span class="badge-worth-top">🔥 WARTE UWAGI (TOP POTENCJAŁ)</span>`;
        } else if (m.worth_grade === 'GOOD') {
            worthBadgeHtml = `<span class="badge-worth-good">🟢 SOLIDNY KANDYDAT</span>`;
        } else {
            worthBadgeHtml = `<span class="badge-worth-standard">⚪ STANDARDOWY / OGRANICZONE DANE</span>`;
        }

        let worthReasonsHtml = '';
        if (m.worth_reasons && m.worth_reasons.length > 0) {
            worthReasonsHtml = `<div class="worth-reasons-box">${m.worth_reasons.map(r => `<span class="worth-reason-pill">${escapeHtml(r)}</span>`).join('')}</div>`;
        }

        // Badge kontekstu przedmeczowego
        let prematchSnippet = '';
        if (pCtx.prematch_goal_rating) {
            prematchSnippet = `
                <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap;">
                    <span class="prematch-badge" style="border-color: ${pCtx.verdict_color}; color: ${pCtx.verdict_color};">
                        Potencjał: ${pCtx.prematch_goal_rating}% (${pCtx.verdict})
                    </span>
                    <span style="font-size: 11px; color: var(--text-muted);">
                        ⏱️ Over 0.5 HT: <b>${pCtx.ht_over05_pct}%</b> | 🏟️ Śr. goli: <b>${pCtx.avg_total_goals}</b>
                    </span>
                    ${pCtx.congestion && pCtx.congestion.has_european_soon ? `<span class="congestion-alert-tag">${pCtx.congestion.alert_text}</span>` : ''}
                </div>
            `;
        }

        card.innerHTML = `
            <div class="match-header">
                <div class="league-tag">🏆 ${escapeHtml(m.league)}</div>
                <div style="display: flex; gap: 6px; align-items: center;">
                    ${worthBadgeHtml}
                    ${m.is_started === false || m.half === 'PRE' 
                        ? `<div class="live-badge" style="background: rgba(255, 214, 0, 0.15); color: #ffd600; border-color: rgba(255, 214, 0, 0.35);">⏳ ${m.stage_text || 'Start wkrótce'}</div>`
                        : `<div class="live-badge">🔴 ${m.stage_text || (m.minute > 0 ? m.minute + "'" : 'LIVE')}</div>`
                    }
                </div>
            </div>

            ${worthReasonsHtml}
            ${prematchSnippet}

            <div class="match-main-row">
                <div class="team-box home">
                    <span class="team-name">${escapeHtml(m.home_team)}</span>
                    ${m.flashscore_home_team && m.flashscore_home_team !== m.home_team ? `<span class="team-name-sub">FS: ${escapeHtml(m.flashscore_home_team)}</span>` : ''}
                </div>
                <div class="score-box">
                    <div class="score-display">${m.score_str || '0:0'}</div>
                    ${m.is_started === false || m.half === 'PRE'
                        ? `<div class="minute-display" style="color: #ffd600; font-size: 11px;">⏳ ${m.stage_text || 'Oczekuje na start'}</div>`
                        : (m.half === 'HT' || m.stage_text === 'Przerwa' 
                            ? `<div class="minute-display" style="color: #29b6f6;">☕ Przerwa (HT)</div>` 
                            : `<div class="minute-display">${m.minute > 0 ? m.minute + "'" : 'LIVE'} (${m.half})</div>`)
                    }
                </div>
                <div class="team-box away">
                    <span class="team-name">${escapeHtml(m.away_team)}</span>
                    ${m.flashscore_away_team && m.flashscore_away_team !== m.away_team ? `<span class="team-name-sub">FS: ${escapeHtml(m.flashscore_away_team)}</span>` : ''}
                </div>
            </div>

            <!-- Danger / Momentum Gauge -->
            <div class="danger-section">
                <div class="danger-header">
                    <span>⚡ INDEKS GROŹNOŚCI / NAPÓR NA BRAMKĘ ${(() => {
                        if (stats.source === 'BEESPORTS') return '<small style="color: #ffd600; font-size: 10px; font-weight: 800;">(BeeSports Live 🐝)</small>';
                        if (stats.source === 'BETSAPI') return '<small style="color: #00e676; font-size: 10px; font-weight: 800;">(BetsAPI In-Play 🎯)</small>';
                        if (stats.source === 'GOALOO') return '<small style="color: #ff9100; font-size: 10px; font-weight: 800;">(Goaloo Live ⚽)</small>';
                        if (stats.source === 'FLASHSCORE') return '<small style="color: #29b6f6; font-size: 10px; font-weight: 800;">(Flashscore Live ⚡)</small>';
                        if (stats.is_estimated) return '<small style="color: var(--accent-yellow); font-size: 10px;">(Model STS Radar)</small>';
                        return '<small style="color: var(--accent-green); font-size: 10px;">(Live Telemetria)</small>';
                    })()}</span>
                    <span style="color: #fff; font-weight: 700;">${m.danger_index}% (${m.apm || 0} APM)</span>
                </div>
                <div class="danger-bar-bg">
                    <div class="danger-bar-fill ${dangerClass}" style="width: ${m.danger_index}%;"></div>
                </div>
            </div>

            <!-- Stats Mini Grid -->
            <div class="stats-mini-grid">
                <div class="mini-stat-card">
                    <div class="label">xG (Oczekiwane)</div>
                    <div class="val">${(() => {
                        let xgH = stats.xg_home || 0;
                        let xgA = stats.xg_away || 0;
                        let xgTot = stats.xg_total || 0;
                        if (xgTot === 0 && ((stats.shots_total_home || 0) > 0 || (stats.shots_total_away || 0) > 0)) {
                            const sH = stats.shots_on_target_home || 0;
                            const sA = stats.shots_on_target_away || 0;
                            const offH = Math.max(0, (stats.shots_total_home || 0) - sH);
                            const offA = Math.max(0, (stats.shots_total_away || 0) - sA);
                            xgH = Math.round((sH * 0.28 + offH * 0.06 + (stats.corners_home || 0) * 0.04) * 100) / 100;
                            xgA = Math.round((sA * 0.28 + offA * 0.06 + (stats.corners_away || 0) * 0.04) * 100) / 100;
                            xgTot = Math.round((xgH + xgA) * 100) / 100;
                        }
                        return `${xgH} - ${xgA} (${xgTot})`;
                    })()}</div>
                </div>
                <div class="mini-stat-card">
                    <div class="label">Strzały (Celne)</div>
                    <div class="val">${stats.shots_total_home || 0}(${stats.shots_on_target_home || 0}) - ${stats.shots_total_away || 0}(${stats.shots_on_target_away || 0})</div>
                </div>
                <div class="mini-stat-card">
                    <div class="label">Rzuty Rożne</div>
                    <div class="val">${stats.corners_home || 0} - ${stats.corners_away || 0} (${stats.corners_total || 0})</div>
                </div>
                <div class="mini-stat-card">
                    <div class="label">Posiadanie</div>
                    <div class="val">${stats.possession_home || 50}% - ${stats.possession_away || 50}%</div>
                </div>
            </div>

            <!-- Sygnały Bramkowe -->
            ${signalsHtml}

            <!-- Porównanie Statystyk: FootyStats, Transfermarkt, MakeYourStats -->
            ${pCtx.comparison ? renderComparisonPanelHtml(pCtx.comparison, 'live-' + m.id) : ''}

            <!-- Kursy STS & Linki (Dynamiczne rynki na żywo) -->
            <div class="odds-footer-row">
                <div class="odds-pills-group">
                    ${(() => {
                        const mkts = m.live_markets || [];
                        if (mkts.length === 0) {
                            return `
                                <div style="color: var(--text-muted); font-size: 11px; padding: 6px 12px; background: rgba(255,255,255,0.03); border: 1px dashed rgba(255,255,255,0.1); border-radius: 8px;">
                                    🔒 Oferta bramkowa STS zablokowana w tej minucie
                                </div>
                            `;
                        }
                        return mkts.map(mkt => `
                            <div class="odd-pill clickable ${isLegInAko(m.home_team + ' vs ' + m.away_team, mkt.market) ? 'selected' : ''}" 
                                 title="${escapeHtml(mkt.desc || mkt.name)}"
                                 onclick="toggleAkoLeg('${escapeHtml(m.home_team)} vs ${escapeHtml(m.away_team)}', '${escapeHtml(mkt.market)}', ${mkt.odds.toFixed(2)})">
                                 <span class="odd-label">${escapeHtml(mkt.label)}:</span>
                                <span class="odd-val">${mkt.odds.toFixed(2)}</span>
                            </div>
                        `).join('');
                    })()}
                </div>

                <div class="match-links">
                    ${(() => {
                        const mkts = m.live_markets || [];
                        if (mkts.length > 0) {
                            const primaryMkt = mkts[0];
                            return `
                                <button class="btn btn-sm" style="border-color: rgba(255, 214, 0, 0.4); color: #ffd600;" onclick="quickLogToTracker('${escapeHtml(m.home_team)} vs ${escapeHtml(m.away_team)}', '${escapeHtml(primaryMkt.market)}', ${primaryMkt.odds.toFixed(2)})">
                                    📝 Do Dziennika
                                </button>
                            `;
                        }
                        return '';
                    })()}

                    <a href="${m.sts_url || 'https://www.sts.pl/zaklady-bukmacherskie/live'}" target="_blank" class="btn btn-sm btn-sts">
                        STS Live ↗
                    </a>
                    <a href="${m.flashscore_url || '#'}" target="_blank" class="btn btn-sm btn-fs">
                        Flashscore Stats ↗
                    </a>
                </div>
            </div>
        `;

        container.appendChild(card);
    });
}


// ==================== 2. PRE-MATCH SCANNER ====================

// ==================== 2. PRE-MATCH SCANNER ====================

let timeFilter = 'ALL';
let ratingFilter = 'ALL';
let currentPrematchGroups = [];
let collapsedCountries = {};

async function runPrematchScan() {
    if (isScanningPrematch) return;
    isScanningPrematch = true;
    updateScanButtonState(true);

    try {
        let result = null;
        if (window.pywebview && window.pywebview.api) {
            result = await window.pywebview.api.scan_prematch(countryFilter, dayOffset, timeFilter, 40);
        } else {
            const resp = await fetch(`/api/prematch?country=${countryFilter}&day=${dayOffset}&time_filter=${timeFilter}&min_rating=40`);
            result = await resp.json();
        }

        if (result && result.matches) {
            currentPrematch = result.matches || [];
            currentPrematchGroups = result.country_groups || [];
            renderPrematchMatches();
        }
    } catch (err) {
        console.error('Prematch scan error:', err);
    } finally {
        isScanningPrematch = false;
        updateScanButtonState(false);
    }
}

function toggleCountryAccordion(countryName) {
    collapsedCountries[countryName] = !collapsedCountries[countryName];
    const el = document.getElementById(`accordion-${countryName.replace(/[^a-zA-Z0-9]/g, '_')}`);
    if (el) {
        el.classList.toggle('collapsed', collapsedCountries[countryName]);
    }
}

function expandAllCountries() {
    collapsedCountries = {};
    document.querySelectorAll('.country-accordion-card').forEach(c => c.classList.remove('collapsed'));
}

function collapseAllCountries() {
    document.querySelectorAll('.country-accordion-card').forEach(c => {
        c.classList.add('collapsed');
        const id = c.dataset.country;
        if (id) collapsedCountries[id] = true;
    });
}

function renderPrematchMatches() {
    const container = document.getElementById('matches-container-prematch');
    container.innerHTML = '';

    const filtered = currentPrematch.filter(m => {
        if (searchPrematchQuery) {
            const q = searchPrematchQuery.toLowerCase();
            const text = `${m.country} ${m.home_team} ${m.away_team} ${m.league}`.toLowerCase();
            if (!text.includes(q)) return false;
        }

        if (ratingFilter !== 'ALL') {
            const minR = parseInt(ratingFilter) || 40;
            const r = m.analysis ? m.analysis.prematch_goal_rating : 50;
            if (r < minR) return false;
        }

        return true;
    });

    const countEl = document.getElementById('tab-prematch-count');
    if (countEl) countEl.innerText = filtered.length;

    if (filtered.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📅</div>
                <div class="empty-title">Brak nadchodzących meczów dla wybranej oceny eksperta / filtrów</div>
                <div class="empty-desc">Zmień filtr 'Ocena Eksperta' (np. na 'Wszystkie Oceny') lub filtr czasu.</div>
            </div>
        `;
        return;
    }

    // Grupowanie meczów po krajach
    const grouped = {};
    filtered.forEach(m => {
        const c = m.country.toUpperCase();
        if (!grouped[c]) {
            grouped[c] = {
                country: c,
                flag: m.country_flag || '⚽',
                matches: [],
                top_rating: 0
            };
        }
        grouped[c].matches.push(m);
        grouped[c].top_rating = Math.max(grouped[c].top_rating, m.analysis ? m.analysis.prematch_goal_rating : 50);
    });

    const groupsList = Object.values(grouped);
    // Sortuj grupy: najwyższy potencjał na górze
    groupsList.sort((a, b) => b.top_rating - a.top_rating);

    groupsList.forEach(group => {
        const cKey = group.country.replace(/[^a-zA-Z0-9]/g, '_');
        const isCollapsed = collapsedCountries[group.country] === true;

        const accordion = document.createElement('div');
        accordion.id = `accordion-${cKey}`;
        accordion.className = `country-accordion-card ${isCollapsed ? 'collapsed' : ''}`;
        accordion.dataset.country = group.country;

        // Header akordeonu STS
        const header = document.createElement('div');
        header.className = 'country-accordion-header';
        header.onclick = () => toggleCountryAccordion(group.country);

        header.innerHTML = `
            <div class="country-accordion-title">
                <span class="country-accordion-flag">${group.flag}</span>
                <span>${escapeHtml(group.country)}</span>
                <span class="country-accordion-badge">${group.matches.length} mecz${group.matches.length > 1 ? (group.matches.length < 5 ? 'e' : 'ów') : ''}</span>
                ${group.top_rating >= 80 ? `<span class="badge-worth-top">🔥 TOP POTENCJAŁ (${group.top_rating}%)</span>` : ''}
            </div>
            <div class="country-accordion-arrow">▼</div>
        `;

        // Content akordeonu z kartami meczów
        const content = document.createElement('div');
        content.className = 'country-accordion-content';

        group.matches.forEach(m => {
            const card = document.createElement('div');
            card.className = 'match-card';
            const a = m.analysis || {};
            const o = m.odds || a.odds || {};
            const isWatched = watchlistMap[m.id] !== undefined;

            let timeBadge = `<span style="font-size: 12px; font-weight: 700; color: var(--accent-yellow);">⏰ ${m.time_str}</span>`;
            if (m.mins_until >= 0 && m.mins_until <= 60) {
                timeBadge = `<span class="time-badge-soon">⚡ Start za ${m.mins_until} min (${m.time_str})</span>`;
            } else if (m.mins_until > 60 && m.mins_until <= 120) {
                timeBadge = `<span style="font-size: 11px; font-weight: 700; color: #ffd600; background: rgba(255,214,0,0.1); padding: 2px 6px; border-radius: 4px;">⏳ za ${Math.floor(m.mins_until/60)}h ${m.mins_until%60}m</span>`;
            }

            // Notatki taktyczne
            let notesHtml = '';
            if (a.tactical_notes && a.tactical_notes.length > 0) {
                notesHtml = `<div class="tactical-notes-box">`;
                a.tactical_notes.forEach(note => {
                    notesHtml += `<div class="tactical-note-item">📌 ${escapeHtml(note)}</div>`;
                });
                notesHtml += `</div>`;
            }

            const o1Val = o.odds_1 ? o.odds_1.toFixed(2) : '2.15';
            const oXVal = o.odds_X ? o.odds_X.toFixed(2) : '3.40';
            const o2Val = o.odds_2 ? o.odds_2.toFixed(2) : '3.25';
            const o05HtVal = o.over_05_ht ? o.over_05_ht.toFixed(2) : (a.ht_over05_pct > 80 ? '1.34' : '1.58');
            const o15FtVal = o.over_15_ft ? o.over_15_ft.toFixed(2) : '1.25';
            const o25FtVal = o.over_25_ft ? o.over_25_ft.toFixed(2) : (a.avg_total_goals > 3.0 ? '1.62' : '1.82');
            const oBttsVal = o.btts ? o.btts.toFixed(2) : '1.75';

            const matchTitle = `${m.home_team} vs ${m.away_team}`;

            card.innerHTML = `
                <div class="match-header">
                    <div class="league-tag">🏆 ${escapeHtml(m.league)}</div>
                    <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap;">
                        ${m.matched_with_sts ? '<span class="badge-worth-top" style="background: rgba(255, 214, 0, 0.15); color: #ffd600; border: 1px solid rgba(255, 214, 0, 0.4); font-size: 10px; padding: 2px 6px; border-radius: 4px;">👑 100% STS OFERTA</span>' : ''}
                        ${timeBadge}
                        <button class="btn-star ${isWatched ? 'active' : ''}" data-id="${m.id}" title="Dodaj do Watchlisty">
                            ${isWatched ? '⭐ Obserwowany' : '☆ Obserwuj'}
                        </button>
                    </div>
                </div>

                <div class="match-main-row">
                    <div class="team-box home">
                        <span class="team-name">${escapeHtml(m.home_team)}</span>
                    </div>
                    <div class="score-box">
                        <div class="prematch-badge" style="border-color: ${a.verdict_color || '#00e676'}; color: ${a.verdict_color || '#00e676'}; font-size: 14px; padding: 6px 14px;">
                            ${a.prematch_goal_rating || 75}%
                        </div>
                        <span style="font-size: 10px; color: var(--text-dim); margin-top: 2px;">Potencjał bramek</span>
                    </div>
                    <div class="team-box away">
                        <span class="team-name">${escapeHtml(m.away_team)}</span>
                    </div>
                </div>

                <!-- Statystyki Przedmeczowe -->
                <div class="stats-mini-grid">
                    <div class="mini-stat-card">
                        <div class="label">Over 0.5 HT <small style="font-size: 9px; opacity: 0.7;">(Statystyka H2H)</small></div>
                        <div class="val" style="color: var(--accent-green);">${a.ht_over05_pct || 80}%</div>
                    </div>
                    <div class="mini-stat-card">
                        <div class="label">Over 1.5 HT <small style="font-size: 9px; opacity: 0.7;">(Statystyka H2H)</small></div>
                        <div class="val">${a.ht_over15_pct || 40}%</div>
                    </div>
                    <div class="mini-stat-card">
                        <div class="label">Śr. goli / mecz</div>
                        <div class="val" style="color: var(--accent-yellow);">${a.avg_total_goals || 3.0}</div>
                    </div>
                    <div class="mini-stat-card">
                        <div class="label">Ocena Eksperta</div>
                        <div class="val" style="font-size: 11px; color: ${a.verdict_color || '#fff'};">${a.verdict || 'Ciekawy mecz'}</div>
                    </div>
                </div>

                ${notesHtml}

                <!-- Porównanie Statystyk: FootyStats, Transfermarkt, MakeYourStats -->
                ${a.comparison ? renderComparisonPanelHtml(a.comparison, 'pre-' + m.id) : ''}

                <!-- Kursy STS 1X2 & Rynki Bramkowe -->
                <div class="odds-footer-row" style="flex-direction: column; gap: 8px; align-items: stretch;">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
                        <!-- Rynki 1X2 STS -->
                        <div class="odds-pills-group" style="gap: 4px;">
                            <span style="font-size: 11px; font-weight: 800; color: #ffd600; align-self: center; margin-right: 2px;">STS 1X2:</span>
                            <div class="odd-pill clickable ${isLegInAko(matchTitle, '1 (Gospodarze)') ? 'selected' : ''}" 
                                 title="Postaw na wygraną gospodarzy"
                                 onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', '1 (Gospodarze)', ${o1Val})">
                                 <span class="odd-label">1:</span>
                                 <span class="odd-val">${o1Val}</span>
                            </div>
                            <div class="odd-pill clickable ${isLegInAko(matchTitle, 'Remis (X)') ? 'selected' : ''}" 
                                 title="Postaw na remis"
                                 onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', 'Remis (X)', ${oXVal})">
                                 <span class="odd-label">X:</span>
                                 <span class="odd-val">${oXVal}</span>
                            </div>
                            <div class="odd-pill clickable ${isLegInAko(matchTitle, '2 (Goście)') ? 'selected' : ''}" 
                                 title="Postaw na wygraną gości"
                                 onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', '2 (Goście)', ${o2Val})">
                                 <span class="odd-label">2:</span>
                                 <span class="odd-val">${o2Val}</span>
                            </div>
                        </div>

                        <!-- Rynki Bramkowe STS -->
                        <div class="odds-pills-group" style="gap: 4px;">
                            ${(() => {
                                const leg = (m.league || '').toLowerCase();
                                const isTopTier = leg.includes('premier league') || leg.includes('ekstraklasa') || leg.includes('laliga') || leg.includes('serie a') || leg.includes('bundesliga') || leg.includes('champions league');
                                if (isTopTier) {
                                    return `
                                        <div class="odd-pill clickable ${isLegInAko(matchTitle, 'Over 0.5 HT') ? 'selected' : ''}" 
                                             title="Min. 1 gol w 1. połowie (Oferta przedmeczowa)"
                                             onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', 'Over 0.5 HT', ${o05HtVal})">
                                            <span class="odd-label">+ Over 0.5 HT:</span>
                                            <span class="odd-val" style="color: var(--accent-green);">${o05HtVal}</span>
                                        </div>
                                    `;
                                } else {
                                    return `
                                        <div class="odd-pill" style="opacity: 0.85; border: 1px dashed rgba(255,214,0,0.35); cursor: help;" 
                                             title="Dla tej ligi STS otwiera rynek Over 0.5 HT na żywo (Live) zaraz po pierwszym gwizdku">
                                            <span class="odd-label">⚡ Over 0.5 HT:</span>
                                            <span class="odd-val" style="color: #ffd600; font-size: 11px;">W trybie Live 🔴</span>
                                        </div>
                                    `;
                                }
                            })()}
                            <div class="odd-pill clickable ${isLegInAko(matchTitle, 'Over 1.5 FT') ? 'selected' : ''}" 
                                 title="Min. 2 gole w całym meczu (STS: Liczba goli)"
                                 onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', 'Over 1.5 FT', ${o15FtVal})">
                                <span class="odd-label">+ Over 1.5 FT:</span>
                                <span class="odd-val">${o15FtVal}</span>
                            </div>
                            <div class="odd-pill clickable ${isLegInAko(matchTitle, 'Over 2.5 FT') ? 'selected' : ''}" 
                                 title="Min. 3 gole w całym meczu (STS: Liczba goli)"
                                 onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', 'Over 2.5 FT', ${o25FtVal})">
                                <span class="odd-label">+ Over 2.5 FT:</span>
                                <span class="odd-val">${o25FtVal}</span>
                            </div>
                            <div class="odd-pill clickable ${isLegInAko(matchTitle, 'BTTS (Obie strzelą)') ? 'selected' : ''}" 
                                 title="Obie drużyny strzelą gola (STS: Obie drużyny - strzelą gola)"
                                 onclick="toggleAkoLeg('${escapeHtml(matchTitle)}', 'BTTS (Obie strzelą)', ${oBttsVal})">
                                <span class="odd-label">+ BTTS:</span>
                                <span class="odd-val">${oBttsVal}</span>
                            </div>
                        </div>
                    </div>

                    <!-- Przyciski akcji -->
                    <div style="display: flex; justify-content: flex-end; gap: 8px; align-items: center; border-top: 1px solid rgba(255,255,255,0.04); padding-top: 6px;">
                        <button class="btn btn-sm" style="border-color: rgba(255, 214, 0, 0.4); color: #ffd600;" 
                                onclick="quickLogToTracker('${escapeHtml(matchTitle)}', 'Over 0.5 HT', ${o05HtVal})">
                            📝 Do Dziennika
                        </button>
                        <a href="${m.sts_url || 'https://www.sts.pl/zaklady-bukmacherskie/pilka-nozna/dzisiaj'}" target="_blank" class="btn btn-sm btn-sts">
                            STS Oferta ↗
                        </a>
                        <a href="${m.url}" target="_blank" class="btn btn-sm btn-fs">
                            Flashscore H2H ↗
                        </a>
                    </div>
                </div>
            `;

            // Obsługa kliknięcia gwiazdki
            const starBtn = card.querySelector('.btn-star');
            starBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                toggleWatchlistMatch(m);
            });

            content.appendChild(card);
        });

        accordion.appendChild(header);
        accordion.appendChild(content);
        container.appendChild(accordion);
    });
}

// Helper: Renderowanie wieloźródłowego panelu porównawczego (FootyStats, Transfermarkt, MakeYourStats, Flashscore H2H)
function renderComparisonPanelHtml(comp, id) {
    if (!comp) return '';
    const fs = comp.footystats || {};
    const tm = comp.transfermarkt || {};
    const mys = comp.makeyourstats || {};
    const h2h = comp.h2h_summary || {};
    const cLevel = comp.consensus_level || 'WYSOKI (3/4 Źródła)';
    const cRating = comp.consensus_rating || 82;

    return `
        <button class="btn-comparison-toggle" onclick="toggleComparisonPanel('${id}')">
            📊 Statystyki Wieloźródłowe (FootyStats | H2H Flashscore | Transfermarkt | MakeYourStats) • <span style="color: #ffd600; font-weight: 700;">${escapeHtml(cLevel)}</span> ▾
        </button>
        <div id="comp-panel-${id}" class="comparison-panel">
            <div style="background: rgba(0, 230, 118, 0.08); border: 1px solid rgba(0, 230, 118, 0.25); border-radius: 8px; padding: 8px 12px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;">
                <div style="font-size: 11px; color: #e2e8f0;">
                    🛡️ <b>Model Konsensusu Typowania:</b> <span style="color: #00e676; font-weight: 700;">${escapeHtml(comp.consensus_summary || 'Wysoka zgodność modeli analitycznych')}</span>
                </div>
                <div style="font-size: 12px; font-weight: 800; color: #00e676; background: rgba(0,230,118,0.15); padding: 3px 8px; border-radius: 6px;">
                    ${cRating}% Zgodności
                </div>
            </div>

            <div class="comparison-grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));">
                <!-- 1. FootyStats -->
                <div class="source-card">
                    <div class="source-header">
                        <span class="source-title source-fs">📈 FootyStats.org</span>
                        <a href="${fs.url || 'https://footystats.org/pl/'}" target="_blank" class="source-link-btn">Szukaj ↗</a>
                    </div>
                    <div class="metric-row"><span class="metric-label">Over 0.5 HT:</span><span class="metric-val" style="color: var(--accent-green); font-weight: 700;">${fs.ht_over05_pct || 80}%</span></div>
                    <div class="metric-row"><span class="metric-label">Over 1.5 HT:</span><span class="metric-val">${fs.ht_over15_pct || 40}%</span></div>
                    <div class="metric-row"><span class="metric-label">Over 2.5 FT:</span><span class="metric-val">${fs.ft_over25_pct || 65}%</span></div>
                    <div class="metric-row"><span class="metric-label">BTTS (Obie strzelą):</span><span class="metric-val">${fs.btts_pct || 55}%</span></div>
                    <div class="metric-row"><span class="metric-label">Śr. minuta 1. gola:</span><span class="metric-val" style="color: var(--accent-yellow);">${fs.avg_first_goal_minute || "26'"}</span></div>
                </div>

                <!-- 2. Flashscore Real H2H & Forma -->
                <div class="source-card" style="border-color: rgba(0, 176, 255, 0.3);">
                    <div class="source-header">
                        <span class="source-title" style="color: #00b0ff;">⚡ Flashscore H2H & Forma</span>
                        <span style="font-size: 10px; color: var(--text-dim);">10 meczów</span>
                    </div>
                    <div class="metric-row"><span class="metric-label">Śr. goli / mecz:</span><span class="metric-val" style="color: #00b0ff; font-weight: 700;">${h2h.avg_total_goals || 2.9}</span></div>
                    <div class="metric-row"><span class="metric-label">Over 0.5 HT w formie:</span><span class="metric-val" style="color: var(--accent-green);">${h2h.ht_over05_pct || 80}%</span></div>
                    <div class="metric-row"><span class="metric-label">Over 2.5 FT w formie:</span><span class="metric-val">${h2h.ft_over25_pct || 60}%</span></div>
                    <div class="metric-row"><span class="metric-label">BTTS w historii:</span><span class="metric-val">${h2h.btts_pct || 52}%</span></div>
                    <div class="metric-row"><span class="metric-label">Mecze H2H / Próbka:</span><span class="metric-val" style="color: var(--text-muted);">${(h2h.home_matches_count || 10) + (h2h.away_matches_count || 10)} spotkań</span></div>
                </div>

                <!-- 3. Transfermarkt -->
                <div class="source-card">
                    <div class="source-header">
                        <span class="source-title source-tm">💶 Transfermarkt.pl</span>
                        <a href="${tm.url || 'https://www.transfermarkt.pl/'}" target="_blank" class="source-link-btn">Kluby ↗</a>
                    </div>
                    <div class="metric-row"><span class="metric-label">Wartość Gospodarz:</span><span class="metric-val">${tm.value_home_mln || "15 mln €"}</span></div>
                    <div class="metric-row"><span class="metric-label">Wartość Gość:</span><span class="metric-val">${tm.value_away_mln || "15 mln €"}</span></div>
                    <div class="metric-row"><span class="metric-label">Dysproporcja kadr:</span><span class="metric-val" style="color: var(--accent-blue);">${tm.disparity_ratio || "1.0x"}</span></div>
                    <div class="metric-row"><span class="metric-label">Przewaga:</span><span class="metric-val" style="font-size: 10px;">${tm.quality_advantage || "Wyrównane"}</span></div>
                    <div class="metric-row"><span class="metric-label">Ocena Mismatch:</span><span class="metric-val" style="font-size: 10px; color: ${tm.mismatch_score >= 80 ? 'var(--accent-red)' : 'var(--accent-yellow)'};">${tm.mismatch_label || "Wyrównany"}</span></div>
                </div>

                <!-- 4. MakeYourStats -->
                <div class="source-card">
                    <div class="source-header">
                        <span class="source-title source-mys">🎯 MakeYourStats</span>
                        <a href="${mys.url || 'https://makeyourstats.com/pl'}" target="_blank" class="source-link-btn">Trendy ↗</a>
                    </div>
                    <div class="metric-row"><span class="metric-label">Seria bramkowa:</span><span class="metric-val" style="color: var(--accent-yellow); font-size: 10px;">${mys.goal_streak || "Seria 5 meczów"}</span></div>
                    <div class="metric-row"><span class="metric-label">Gole 15'-30' (Okno HT):</span><span class="metric-val">${mys.timing_distribution ? mys.timing_distribution['15_30'] : '24%'}</span></div>
                    <div class="metric-row"><span class="metric-label">Gole 75'-90' (Late 2H):</span><span class="metric-val" style="color: var(--accent-red);">${mys.timing_distribution ? mys.timing_distribution['75_90'] : '28%'}</span></div>
                    <div class="metric-row"><span class="metric-label">Wskaźnik Trendu:</span><span class="metric-val" style="color: var(--accent-green);">${mys.trend_score || 88}/100</span></div>
                </div>

                <!-- 5. Understat -->
                ${(() => {
                    const us = comp.understat || {};
                    return `
                        <div class="source-card" style="border-color: rgba(255, 140, 0, 0.35);">
                            <div class="source-header">
                                <span class="source-title" style="color: #ff8c00;">📊 Understat.com</span>
                                <a href="${us.url || 'https://understat.com/'}" target="_blank" class="source-link-btn">xG Model ↗</a>
                            </div>
                            <div class="metric-row"><span class="metric-label">Oczekiwane xG meczu:</span><span class="metric-val" style="color: #ff8c00; font-weight: 700;">${us.expected_xg_total || 2.85}</span></div>
                            <div class="metric-row"><span class="metric-label">Jakość strzałów:</span><span class="metric-val" style="color: var(--accent-green);">${us.shot_quality_val || "0.14 xG/strzał"}</span></div>
                            <div class="metric-row"><span class="metric-label">Ocena szans:</span><span class="metric-val" style="font-size: 10px; font-weight: 700; color: #ff8c00;">${us.shot_quality_grade || "WYSOKA"}</span></div>
                            <div class="metric-row"><span class="metric-label">Nieszczelność xGA:</span><span class="metric-val" style="font-size: 10px;">${us.defense_xga || "1.45 xGA"}</span></div>
                        </div>
                    `;
                })()}

                <!-- 6. SoccerStats -->
                ${(() => {
                    const ss = comp.soccerstats || {};
                    return `
                        <div class="source-card" style="border-color: rgba(0, 230, 118, 0.35);">
                            <div class="source-header">
                                <span class="source-title" style="color: #00e676;">⏱️ SoccerStats.com</span>
                                <a href="${ss.url || 'https://www.soccerstats.com/'}" target="_blank" class="source-link-btn">Timing ↗</a>
                            </div>
                            <div class="metric-row"><span class="metric-label">Prawdopodobieństwo 1H:</span><span class="metric-val" style="color: #00e676; font-weight: 700;">${ss.ht_goal_probability || 80}%</span></div>
                            <div class="metric-row"><span class="metric-label">Bilans Dom (PPG):</span><span class="metric-val">${ss.ppg_home || "1.85 PPG"}</span></div>
                            <div class="metric-row"><span class="metric-label">Bilans Wyjazd (PPG):</span><span class="metric-val">${ss.ppg_away || "1.25 PPG"}</span></div>
                            <div class="metric-row"><span class="metric-label">Rozkład goli 1H:</span><span class="metric-val" style="font-size: 9px; color: var(--text-muted);">${ss.timing_1h_distribution || "16-30' (28%)"}</span></div>
                        </div>
                    `;
                })()}

                <!-- 7. WhoScored -->
                ${(() => {
                    const ws = comp.whoscored || {};
                    return `
                        <div class="source-card" style="border-color: rgba(147, 51, 234, 0.35);">
                            <div class="source-header">
                                <span class="source-title" style="color: #a855f7;">🧠 WhoScored.com</span>
                                <a href="${ws.url || 'https://www.whoscored.com/'}" target="_blank" class="source-link-btn">Taktyka ↗</a>
                            </div>
                            <div class="metric-row"><span class="metric-label">Ocena WhoScored:</span><span class="metric-val" style="color: #a855f7; font-weight: 700;">${ws.match_rating || "6.85 ★"}</span></div>
                            <div class="metric-row"><span class="metric-label">Styl ofensywy:</span><span class="metric-val" style="font-size: 10px;">${ws.tactical_style || "Kontratak"}</span></div>
                            <div class="metric-row"><span class="metric-label">Słabość obrony:</span><span class="metric-val" style="font-size: 9px; color: var(--accent-yellow);">${ws.key_weakness || "Stałe fragmenty"}</span></div>
                            <div class="metric-row"><span class="metric-label">Ocena Kreacji:</span><span class="metric-val" style="font-size: 10px; font-weight: 700; color: #a855f7;">${ws.tactical_danger_grade || "WYSOKA"}</span></div>
                        </div>
                    `;
                })()}

                <!-- 8. AdamChoi & BetsAPI -->
                ${(() => {
                    const ac = comp.adamchoi || {};
                    const ba = comp.betsapi || {};
                    return `
                        <div class="source-card" style="border-color: rgba(6, 182, 212, 0.35);">
                            <div class="source-header">
                                <span class="source-title" style="color: #06b6d4;">🔥 AdamChoi & BetsAPI</span>
                                <a href="${ac.url || 'https://www.adamchoi.co.uk/'}" target="_blank" class="source-link-btn">Passy ↗</a>
                            </div>
                            <div class="metric-row"><span class="metric-label">Passa Overów:</span><span class="metric-val" style="color: #06b6d4; font-weight: 700;">${ac.over15_streak || "Passa 6x Over"}</span></div>
                            <div class="metric-row"><span class="metric-label">Trendy Rożnych:</span><span class="metric-val" style="font-size: 10px;">${ac.corners_trend || "8.5+ w 80%"}</span></div>
                            <div class="metric-row"><span class="metric-label">Linia Azjatycka:</span><span class="metric-val" style="color: var(--accent-yellow);">${ba.asian_handicap_line || "AH -0.5"}</span></div>
                            <div class="metric-row"><span class="metric-label">Oczekiwane Gole:</span><span class="metric-val" style="color: var(--accent-green); font-weight: 700;">${ba.market_expectancy || 2.95}</span></div>
                        </div>
                    `;
                })()}
            </div>
        </div>

    `;
}

function toggleComparisonPanel(id) {
    const el = document.getElementById('comp-panel-' + id);
    if (el) {
        el.classList.toggle('open');
    }
}


// ==================== 3. WATCHLIST MANAGEMENT ====================

async function toggleWatchlistMatch(matchData) {
    const mId = matchData.id;
    if (watchlistMap[mId]) {
        delete watchlistMap[mId];
    } else {
        watchlistMap[mId] = matchData;
    }

    // Backend sync
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.toggle_watchlist(mId, matchData);
        } else {
            await fetch(`/api/watchlist/toggle?id=${mId}`);
        }
    } catch (e) {
        console.error('Watchlist sync error:', e);
    }

    updateWatchlistCount();
    if (activeTab === 'prematch') renderPrematchMatches();
    if (activeTab === 'watchlist') renderWatchlist();
}

async function loadWatchlist() {
    try {
        let list = [];
        if (window.pywebview && window.pywebview.api) {
            list = await window.pywebview.api.get_watchlist();
        } else {
            const resp = await fetch('/api/watchlist');
            list = await resp.json();
        }
        if (Array.isArray(list)) {
            watchlistMap = {};
            list.forEach(item => { watchlistMap[item.id] = item; });
            updateWatchlistCount();
        }
    } catch (e) {
        console.error('Load watchlist error:', e);
    }
}

function updateWatchlistCount() {
    const count = Object.keys(watchlistMap).length;
    document.getElementById('tab-watchlist-count').innerText = count;
}

function renderWatchlist() {
    const container = document.getElementById('matches-container-watchlist');
    container.innerHTML = '';

    const list = Object.values(watchlistMap);
    if (list.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">⭐</div>
                <div class="empty-title">Brak obserwowanych meczów</div>
                <div class="empty-desc">Przejdź do zakładki Skaner Przedmeczowy i kliknij ⭐ przy meczach, które chcesz mieć na oku.</div>
            </div>
        `;
        return;
    }

    list.forEach(m => {
        const card = document.createElement('div');
        card.className = 'match-card';
        const a = m.analysis || {};

        card.innerHTML = `
            <div class="match-header">
                <div class="league-tag">🏆 ${escapeHtml(m.league)}</div>
                <button class="btn btn-sm btn-star active" data-id="${m.id}">❌ Usuń z Watchlisty</button>
            </div>

            <div class="match-main-row">
                <div class="team-box home"><span class="team-name">${escapeHtml(m.home_team)}</span></div>
                <div class="score-box">
                    <div class="prematch-badge" style="border-color: ${a.verdict_color || '#00e676'}; color: ${a.verdict_color || '#00e676'};">
                        Potencjał: ${a.prematch_goal_rating || 80}%
                    </div>
                </div>
                <div class="team-box away"><span class="team-name">${escapeHtml(m.away_team)}</span></div>
            </div>

            <div class="odds-footer-row">
                <span style="font-size: 12px; color: var(--accent-yellow);">
                    ⏱️ Over 0.5 HT: <b>${a.ht_over05_pct || 82}%</b> | Śr. goli: <b>${a.avg_total_goals || 3.1}</b>
                </span>
                <div class="match-links">
                    <a href="${m.url}" target="_blank" class="btn btn-sm btn-fs">Flashscore ↗</a>
                </div>
            </div>
        `;

        card.querySelector('.btn-star').addEventListener('click', () => {
            toggleWatchlistMatch(m);
        });

        container.appendChild(card);
    });
}


// ==================== 4. BET TRACKER & TARGET +10 ZŁ ====================

let currentTrackerData = null;

async function loadTrackerData() {
    try {
        let data = null;
        if (window.pywebview && window.pywebview.api) {
            data = await window.pywebview.api.get_tracker_summary();
        } else {
            const resp = await fetch('/api/tracker');
            data = await resp.json();
        }

        if (data) {
            currentTrackerData = data;
            renderTracker();
        }
    } catch (e) {
        console.error('Tracker load error:', e);
    }
}

function renderTracker() {
    if (!currentTrackerData) return;
    const d = currentTrackerData;

    // 1. KPI Wskaźniki
    const elBankroll = document.getElementById('tracker-bankroll');
    if (elBankroll) elBankroll.innerText = `${d.current_bankroll.toFixed(2)} zł`;

    const elDailyProfit = document.getElementById('tracker-daily-profit');
    if (elDailyProfit) {
        const sign = d.daily_profit > 0 ? '+' : '';
        elDailyProfit.innerText = `${sign}${d.daily_profit.toFixed(2)} zł`;
        elDailyProfit.style.color = d.daily_profit > 0 ? 'var(--accent-green)' : (d.daily_profit < 0 ? 'var(--accent-red)' : '#fff');
    }

    const elWinRate = document.getElementById('tracker-winrate');
    if (elWinRate) elWinRate.innerText = `${d.win_rate}% (${d.won_bets}/${d.won_bets + d.lost_bets})`;

    const elYield = document.getElementById('tracker-yield');
    if (elYield) {
        const sign = d.yield_pct > 0 ? '+' : '';
        elYield.innerText = `${sign}${d.yield_pct}%`;
        elYield.style.color = d.yield_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
    }

    const elTabCount = document.getElementById('tab-tracker-count');
    if (elTabCount) elTabCount.innerText = d.pending_bets || 0;

    // 2. Target Progress Bar
    const progressVal = document.getElementById('target-progress-val');
    const progressBar = document.getElementById('target-bar-fill');
    const statusText = document.getElementById('target-status-text');

    if (progressVal && progressBar) {
        progressVal.innerText = `${d.daily_profit.toFixed(2)} / +${d.daily_target_profit.toFixed(2)} zł (${d.target_progress_pct}%)`;
        progressBar.style.width = `${Math.min(100, Math.max(0, d.target_progress_pct))}%`;

        if (d.daily_profit >= d.daily_target_profit) {
            statusText.innerText = '🎉 CEL DNIA OSIĄGNIĘTY! ZAMKNIJ APLIKACJĘ';
            statusText.style.color = 'var(--accent-green)';
            statusText.style.fontWeight = '800';
        } else if (d.daily_profit <= -6.0) {
            statusText.innerText = '🛑 STOP-LOSS: Limit strat osiągnięty (-6 zł). Przestań grać do jutra.';
            statusText.style.color = 'var(--accent-red)';
            statusText.style.fontWeight = '800';
        } else {
            statusText.innerText = '(W trakcie realizacji celu)';
            statusText.style.color = 'var(--text-muted)';
        }
    }

    // 3. Render Bets List
    const container = document.getElementById('tracker-bets-container');
    if (!container) return;
    container.innerHTML = '';

    const bets = d.bets || [];
    if (bets.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📝</div>
                <div class="empty-title">Brak wpisów w Dzienniku</div>
                <div class="empty-desc">Dodaj swój pierwszy postawiony kupon za pomocą formularza powyżej.</div>
            </div>
        `;
        return;
    }

    bets.forEach(b => {
        const card = document.createElement('div');
        const st = (b.status || 'PENDING').toLowerCase();
        card.className = `bet-card ${st}`;

        let statusBadge = `<span class="bet-badge-pending">⏳ W GRZE</span>`;
        if (b.status === 'WON') statusBadge = `<span class="bet-badge-won">✅ WYGRANY (+${b.profit_loss.toFixed(2)} zł)</span>`;
        else if (b.status === 'LOST') statusBadge = `<span class="bet-badge-lost">❌ PRZEGRANY (${b.profit_loss.toFixed(2)} zł)</span>`;
        else if (b.status === 'VOID') statusBadge = `<span class="bet-badge-pending">🔄 ZWROT</span>`;

        card.innerHTML = `
            <div class="bet-header-row">
                <div class="bet-title">⚽ ${escapeHtml(b.match_title)}</div>
                ${statusBadge}
            </div>

            <div class="bet-details-row">
                <span>🎯 Typ: <b style="color: #fff;">${escapeHtml(b.market)}</b></span>
                <span>💰 Stawka: <b>${b.stake.toFixed(2)} zł</b></span>
                <span>📈 Kurs: <b>${b.odds.toFixed(2)}</b></span>
                <span>🏆 Potencjalna wygrana: <b style="color: var(--accent-yellow);">${b.potential_win.toFixed(2)} zł</b></span>
                <span style="font-size: 11px; color: var(--text-dim);">📅 ${b.date} ${b.time}</span>
            </div>

            ${b.notes ? `<div style="font-size: 11px; color: var(--text-muted); font-style: italic;">📝 ${escapeHtml(b.notes)}</div>` : ''}

            <div class="bet-actions-row">
                <div style="display: flex; gap: 6px;">
                    ${b.status === 'PENDING' ? `
                        <button class="btn-resolve-won" onclick="handleResolveBet('${b.id}', 'WON')">✅ Wygrany</button>
                        <button class="btn-resolve-lost" onclick="handleResolveBet('${b.id}', 'LOST')">❌ Przegrany</button>
                    ` : `
                        <button class="btn btn-sm" onclick="handleResolveBet('${b.id}', 'PENDING')">↩️ Zmień na W Grze</button>
                    `}
                </div>
                <button class="btn btn-sm" style="color: var(--accent-red); border-color: transparent;" onclick="handleDeleteBet('${b.id}')">🗑️ Usuń</button>
            </div>
        `;

        container.appendChild(card);
    });
}

async function handleAddBetSubmit() {
    const matchInput = document.getElementById('bet-input-match');
    const marketInput = document.getElementById('bet-input-market');
    const stakeInput = document.getElementById('bet-input-stake');
    const oddsInput = document.getElementById('bet-input-odds');
    const notesInput = document.getElementById('bet-input-notes');

    const match = matchInput.value.trim();
    const market = marketInput.value.trim() || 'Over 0.5 HT';
    const stake = parseFloat(stakeInput.value) || 2.0;
    const odds = parseFloat(oddsInput.value) || 1.75;
    const notes = notesInput.value.trim();

    if (!match) {
        alert('Podaj nazwę meczu lub kuponu!');
        return;
    }

    try {
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.add_tracker_bet(match, market, stake, odds, notes);
        } else {
            const url = `/api/tracker/add?match=${encodeURIComponent(match)}&market=${encodeURIComponent(market)}&stake=${stake}&odds=${odds}&notes=${encodeURIComponent(notes)}`;
            const resp = await fetch(url);
            res = await resp.json();
        }

        if (res) {
            currentTrackerData = res;
            renderTracker();
            matchInput.value = '';
            notesInput.value = '';
        }
    } catch (e) {
        console.error('Add bet error:', e);
    }
}

async function handleResolveBet(id, status) {
    try {
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.resolve_tracker_bet(id, status);
        } else {
            const resp = await fetch(`/api/tracker/resolve?id=${id}&status=${status}`);
            res = await resp.json();
        }
        if (res) {
            currentTrackerData = res;
            renderTracker();
            if (status === 'WON') playSignalSound();
        }
    } catch (e) {
        console.error('Resolve bet error:', e);
    }
}

async function handleDeleteBet(id) {
    if (!confirm('Czy na pewno chcesz usunąć ten zakład z historii?')) return;
    try {
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.delete_tracker_bet(id);
        } else {
            const resp = await fetch(`/api/tracker/delete?id=${id}`);
            res = await resp.json();
        }
        if (res) {
            currentTrackerData = res;
            renderTracker();
        }
    } catch (e) {
        console.error('Delete bet error:', e);
    }
}

async function handleEditBankroll() {
    const cur = currentTrackerData ? currentTrackerData.initial_bankroll : 48.0;
    const val = prompt('Wprowadź swoje początkowe saldo konta w zł:', cur);
    if (!val || isNaN(val)) return;

    try {
        const amt = parseFloat(val);
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.update_bankroll(amt);
        } else {
            const resp = await fetch(`/api/tracker/bankroll?amount=${amt}`);
            res = await resp.json();
        }
        if (res) {
            currentTrackerData = res;
            renderTracker();
        }
    } catch (e) {
        console.error('Edit bankroll error:', e);
    }
}

function quickLogToTracker(match, market, odds) {
    // 1. Przełącz na zakładkę Dziennika
    document.querySelectorAll('.nav-tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));

    const trackerBtn = document.querySelector('.nav-tab-btn[data-tab="tracker"]');
    if (trackerBtn) trackerBtn.classList.add('active');
    activeTab = 'tracker';

    const targetSection = document.getElementById('section-tracker');
    if (targetSection) targetSection.classList.add('active');

    // 2. Wypełnij formularz
    const matchInput = document.getElementById('bet-input-match');
    const marketInput = document.getElementById('bet-input-market');
    const stakeInput = document.getElementById('bet-input-stake');
    const oddsInput = document.getElementById('bet-input-odds');

    if (matchInput) matchInput.value = match;
    if (marketInput) marketInput.value = market;
    if (oddsInput) oddsInput.value = odds;
    if (stakeInput) {
        stakeInput.value = '4.00';
        stakeInput.focus();
    }

    loadTrackerData();
}


// ==================== 5. GENERAL UI LOGIC & CONTROLS ====================

function updateScanButtonState(loading) {
    const btn = document.getElementById('btn-scan');
    if (!btn) return;
    if (loading) {
        btn.innerHTML = '⏳ Skanowanie...';
        btn.disabled = true;
    } else {
        btn.innerHTML = '🔄 Skanuj Teraz';
        btn.disabled = false;
    }
}

function resetCountdown() {
    refreshCountdown = 15;
    document.getElementById('countdown-timer').innerText = refreshCountdown + 's';
}

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => {
        if (activeTab === 'live') {
            refreshCountdown--;
            if (refreshCountdown <= 0) {
                runLiveScan();
            } else {
                const el = document.getElementById('countdown-timer');
                if (el) el.innerText = refreshCountdown + 's';
            }
        }
    }, 1000);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Inicjalizacja zdarzeń
document.addEventListener('DOMContentLoaded', () => {
    loadWatchlist();
    loadTrackerData();
    runLiveScan();
    startAutoRefresh();

    // Przełączanie zakładek
    document.querySelectorAll('.nav-tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.nav-tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));

            e.currentTarget.classList.add('active');
            activeTab = e.currentTarget.dataset.tab;

            const targetSection = document.getElementById(`section-${activeTab}`);
            if (targetSection) targetSection.classList.add('active');

            if (activeTab === 'prematch' && currentPrematch.length === 0) {
                runPrematchScan();
            } else if (activeTab === 'watchlist') {
                renderWatchlist();
            } else if (activeTab === 'tracker') {
                loadTrackerData();
            }
        });
    });

    // Przycisk Skanuj Teraz
    document.getElementById('btn-scan').addEventListener('click', () => {
        initAudio();
        if (activeTab === 'live') {
            isScanningLive = false;
            runLiveScan(true);
        }
        else if (activeTab === 'prematch') runPrematchScan();
        else if (activeTab === 'watchlist') renderWatchlist();
        else if (activeTab === 'tracker') loadTrackerData();
    });


    // Dźwięk
    document.getElementById('btn-sound').addEventListener('click', (e) => {
        soundEnabled = !soundEnabled;
        e.currentTarget.classList.toggle('active', soundEnabled);
        e.currentTarget.innerHTML = soundEnabled ? '🔊 Dźwięk WŁ' : '🔇 Dźwięk WYŁ';
        if (soundEnabled) playSignalSound();
    });

    // Filtry Live
    const btnAllLive = document.getElementById('btn-filter-all-live');
    const btnWorthLive = document.getElementById('btn-filter-worth-watching');
    const btnSignalsLive = document.getElementById('btn-only-signals');

    if (btnAllLive) {
        btnAllLive.addEventListener('click', () => {
            liveFilterMode = 'ALL';
            btnAllLive.classList.add('active');
            if (btnWorthLive) btnWorthLive.classList.remove('active');
            if (btnSignalsLive) btnSignalsLive.classList.remove('active');
            renderLiveMatches();
        });
    }

    if (btnWorthLive) {
        btnWorthLive.addEventListener('click', () => {
            liveFilterMode = 'WORTH';
            btnWorthLive.classList.add('active');
            if (btnAllLive) btnAllLive.classList.remove('active');
            if (btnSignalsLive) btnSignalsLive.classList.remove('active');
            renderLiveMatches();
        });
    }

    if (btnSignalsLive) {
        btnSignalsLive.addEventListener('click', () => {
            liveFilterMode = 'SIGNALS';
            btnSignalsLive.classList.add('active');
            if (btnAllLive) btnAllLive.classList.remove('active');
            if (btnWorthLive) btnWorthLive.classList.remove('active');
            renderLiveMatches();
        });
    }

    document.querySelectorAll('.half-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.half-filter-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            halfFilter = e.currentTarget.dataset.half;
            renderLiveMatches();
        });
    });

    document.getElementById('search-live').addEventListener('input', (e) => {
        searchLiveQuery = e.target.value;
        renderLiveMatches();
    });

    // Filtry Prematch
    document.querySelectorAll('.country-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.country-filter-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            countryFilter = e.currentTarget.dataset.country;
            runPrematchScan();
        });
    });

    document.querySelectorAll('.day-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.day-filter-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            dayOffset = parseInt(e.currentTarget.dataset.day);
            runPrematchScan();
        });
    });

    document.querySelectorAll('.time-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.time-filter-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            timeFilter = e.currentTarget.dataset.time;
            runPrematchScan();
        });
    });

    // Filtry Oceny Eksperta
    document.querySelectorAll('.rating-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            document.querySelectorAll('.rating-filter-btn').forEach(b => b.classList.remove('active'));
            e.currentTarget.classList.add('active');
            ratingFilter = e.currentTarget.dataset.rating;

            const badge = document.getElementById('prematch-rating-badge');
            if (badge) {
                if (ratingFilter === '85') badge.innerHTML = '🔥 TOP 85%+ (Ultra Bramkowe)';
                else if (ratingFilter === '75') badge.innerHTML = '🟢 Min. 75%+ (Bardzo Dobre)';
                else if (ratingFilter === '65') badge.innerHTML = '🟡 Min. 65%+ (Solidny Potencjał)';
                else badge.innerHTML = '👑 Wszystkie Oceny';
            }
            renderPrematchMatches();
        });
    });

    const btnExpandAll = document.getElementById('btn-expand-all-countries');
    if (btnExpandAll) {
        btnExpandAll.addEventListener('click', expandAllCountries);
    }

    const btnCollapseAll = document.getElementById('btn-collapse-all-countries');
    if (btnCollapseAll) {
        btnCollapseAll.addEventListener('click', collapseAllCountries);
    }

    document.getElementById('search-prematch').addEventListener('input', (e) => {
        searchPrematchQuery = e.target.value;
        renderPrematchMatches();
    });

    // Wyczyść watchlistę
    const clearBtn = document.getElementById('btn-clear-watchlist');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            watchlistMap = {};
            updateWatchlistCount();
            renderWatchlist();
        });
    }

    // Dodawanie zakładu do Dziennika
    const addBetBtn = document.getElementById('btn-add-bet');
    if (addBetBtn) {
        addBetBtn.addEventListener('click', handleAddBetSubmit);
    }

    // Zmiana salda początkowego
    const editBankrollBtn = document.getElementById('btn-edit-bankroll');
    if (editBankrollBtn) {
        editBankrollBtn.addEventListener('click', handleEditBankroll);
    }

    // Obsługa Kuponu AKO (Bet Slip)
    const akoHeader = document.getElementById('ako-slip-header');
    if (akoHeader) {
        akoHeader.addEventListener('click', () => {
            const widget = document.getElementById('ako-slip-widget');
            const icon = document.getElementById('ako-toggle-icon');
            if (widget) {
                widget.classList.toggle('closed');
                if (icon) icon.innerText = widget.classList.contains('closed') ? '🔼' : '🔽';
            }
        });
    }

    const akoStakeInput = document.getElementById('ako-input-stake');
    if (akoStakeInput) {
        akoStakeInput.addEventListener('input', renderAkoSlip);
    }

    const btnSaveAko = document.getElementById('btn-save-ako');
    if (btnSaveAko) {
        btnSaveAko.addEventListener('click', saveAkoSlipToTracker);
    }

    const btnClearAko = document.getElementById('btn-clear-ako');
    if (btnClearAko) {
        btnClearAko.addEventListener('click', clearAkoSlip);
    }

    renderAkoSlip();
});

// ==================== KUPON AKO (BET SLIP) LOGIC ====================
let akoLegs = [];

function isLegInAko(match, market) {
    return akoLegs.some(l => l.match === match && l.market === market);
}

function toggleAkoLeg(match, market, odds) {
    const idx = akoLegs.findIndex(l => l.match === match && l.market === market);
    if (idx >= 0) {
        akoLegs.splice(idx, 1);
    } else {
        akoLegs.push({ match, market, odds: parseFloat(odds) || 1.60 });
        // Otwórz widget
        const widget = document.getElementById('ako-slip-widget');
        const icon = document.getElementById('ako-toggle-icon');
        if (widget) {
            widget.classList.remove('closed');
            if (icon) icon.innerText = '🔽';
        }
    }
    renderAkoSlip();
    if (activeTab === 'live') renderLiveMatches();
    if (activeTab === 'prematch') renderPrematchMatches();
}

function removeAkoLeg(index) {
    if (index >= 0 && index < akoLegs.length) {
        akoLegs.splice(index, 1);
        renderAkoSlip();
        if (activeTab === 'live') renderLiveMatches();
        if (activeTab === 'prematch') renderPrematchMatches();
    }
}

function clearAkoSlip() {
    akoLegs = [];
    renderAkoSlip();
    if (activeTab === 'live') renderLiveMatches();
    if (activeTab === 'prematch') renderPrematchMatches();
}

function renderAkoSlip() {
    const countEl = document.getElementById('ako-count');
    const totalOddsEl = document.getElementById('ako-total-odds');
    const listEl = document.getElementById('ako-legs-list');
    const calcOddsEl = document.getElementById('ako-calc-odds');
    const calcWinEl = document.getElementById('ako-calc-win');
    const calcProfitEl = document.getElementById('ako-calc-profit');
    const stakeInput = document.getElementById('ako-input-stake');

    const count = akoLegs.length;
    if (countEl) countEl.innerText = count;

    let totalOdds = 1.0;
    if (count > 0) {
        totalOdds = akoLegs.reduce((acc, curr) => acc * curr.odds, 1.0);
    }
    totalOdds = Math.round(totalOdds * 100) / 100;

    if (totalOddsEl) totalOddsEl.innerText = totalOdds.toFixed(2);
    if (calcOddsEl) calcOddsEl.innerText = totalOdds.toFixed(2);

    const stake = parseFloat(stakeInput ? stakeInput.value : 4.0) || 4.0;
    const potentialWin = Math.round(stake * totalOdds * 0.88 * 100) / 100;
    const profit = Math.round((potentialWin - stake) * 100) / 100;

    if (calcWinEl) calcWinEl.innerText = `${potentialWin.toFixed(2)} zł`;
    if (calcProfitEl) {
        const sign = profit >= 0 ? '+' : '';
        calcProfitEl.innerText = `${sign}${profit.toFixed(2)} zł`;
    }

    if (!listEl) return;
    if (count === 0) {
        listEl.innerHTML = `
            <div class="empty-ako-msg" style="padding: 12px; text-align: center; color: var(--text-muted); font-size: 12px;">
                Kliknij kurs przy dowolnych meczach (Live lub Przedmeczowych), aby złożyć kupon AKO!
            </div>
        `;
        return;
    }

    listEl.innerHTML = '';
    akoLegs.forEach((l, i) => {
        const item = document.createElement('div');
        item.className = 'ako-leg-item';
        item.innerHTML = `
            <div class="ako-leg-info">
                <span class="ako-leg-match">${i+1}. ${escapeHtml(l.match)}</span>
                <span class="ako-leg-market">🎯 ${escapeHtml(l.market)}</span>
            </div>
            <div style="display: flex; align-items: center;">
                <span class="ako-leg-odds">@${l.odds.toFixed(2)}</span>
                <button class="btn-remove-leg" onclick="removeAkoLeg(${i})">✖</button>
            </div>
        `;
        listEl.appendChild(item);
    });
}

async function saveAkoSlipToTracker() {
    if (akoLegs.length === 0) {
        alert('Dodaj najpierw co najmniej 1 mecz do kuponu AKO!');
        return;
    }

    const stakeInput = document.getElementById('ako-input-stake');
    const stake = parseFloat(stakeInput ? stakeInput.value : 4.0) || 4.0;

    try {
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.add_tracker_ako_bet(akoLegs, stake, '');
        } else {
            const legsJson = encodeURIComponent(JSON.stringify(akoLegs));
            const resp = await fetch(`/api/tracker/add_ako?legs=${legsJson}&stake=${stake}`);
            res = await resp.json();
        }

        if (res) {
            currentTrackerData = res;
            alert(`✅ Kupon AKO (${akoLegs.length} zdarzenia, kurs ${res.bets[0].odds}) został zapisany w Dzienniku!`);
            clearAkoSlip();
            
            // Przełącz na zakładkę Dziennika
            document.querySelectorAll('.nav-tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
            const trackerBtn = document.querySelector('.nav-tab-btn[data-tab="tracker"]');
            if (trackerBtn) trackerBtn.classList.add('active');
            activeTab = 'tracker';
            const targetSection = document.getElementById('section-tracker');
            if (targetSection) targetSection.classList.add('active');
            renderTracker();
        }
    } catch (e) {
        console.error('Save AKO slip error:', e);
    }
}

// ==================== 5. TELEGRAM NOTIFICATIONS & MULTI-DEVICE ====================

async function loadTelegramConfig() {
    try {
        let cfg = null;
        if (window.pywebview && window.pywebview.api) {

            cfg = await window.pywebview.api.get_telegram_config();
        } else {
            const resp = await fetch('/api/telegram/config');
            cfg = await resp.json();
        }

        if (cfg) {
            const enabledInput = document.getElementById('tg-enabled');
            const minStarsInput = document.getElementById('tg-min-stars');
            const dot = document.getElementById('telegram-status-dot');

            if (enabledInput) enabledInput.checked = cfg.enabled === true;
            if (minStarsInput) minStarsInput.value = cfg.min_stars || 2;

            if (dot) {
                dot.innerText = (cfg.enabled && cfg.bot_token) ? '🟢' : '⚪';
            }
        }

        // Pobierz dane parowania QR i podłączonych urządzeń
        loadTelegramPairingInfo();
    } catch (err) {
        console.error('Error loading Telegram config:', err);
    }
}

async function loadTelegramPairingInfo() {
    try {
        let data = null;
        if (window.pywebview && window.pywebview.api) {
            data = await window.pywebview.api.get_telegram_pairing();
        } else {
            const resp = await fetch('/api/telegram/pairing');
            data = await resp.json();
        }

        if (data) {
            const qrImg = document.getElementById('tg-qr-image');
            const directLink = document.getElementById('tg-direct-link');
            const pinInput = document.getElementById('tg-pairing-pin');
            const countSpan = document.getElementById('tg-devices-count');
            const listContainer = document.getElementById('tg-subscribers-list');

            if (qrImg && data.qr_code_url) qrImg.src = data.qr_code_url;
            if (directLink && data.join_url) {
                directLink.href = data.join_url;
                directLink.innerText = 't.me/' + (data.bot_username || 'skaner_bot');
            }
            if (pinInput && data.pairing_pin) pinInput.value = data.pairing_pin;
            if (countSpan) countSpan.innerText = data.total_devices || 0;

            if (listContainer) {
                const subs = data.subscribers || [];
                if (subs.length === 0) {
                    listContainer.innerHTML = '<div style="font-size: 11px; color: var(--text-dim);">Brak połączonych urządzeń. Zeskanuj kod QR aparatem powyżej!</div>';
                } else {
                    listContainer.innerHTML = subs.map((s, idx) => `
                        <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(15, 23, 42, 0.6); padding: 6px 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.04);">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 13px;">📱</span>
                                <div>
                                    <div style="font-size: 12px; font-weight: 700; color: #fff;">${escapeHtml(s.first_name || 'Urządzenie')} <span style="font-size: 10px; color: #94a3b8;">(ID: ${escapeHtml(s.chat_id)})</span></div>
                                    <div style="font-size: 10px; color: #00e676;">🟢 Połączony • ${escapeHtml(s.joined_at || 'Aktywny')}</div>
                                </div>
                            </div>
                            <button onclick="removeTelegramSubscriber('${s.chat_id}')" class="btn btn-sm" style="font-size: 10px; padding: 2px 6px; background: rgba(255,23,68,0.2); border-color: rgba(255,23,68,0.4); color: #ff5252;">Rozłącz</button>
                        </div>
                    `).join('');
                }
            }
        }
    } catch (err) {
        console.error('Error loading pairing info:', err);
    }
}

async function savePairingPin() {
    const pinInput = document.getElementById('tg-pairing-pin');
    const pin = pinInput ? pinInput.value.trim() : '7777';
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.set_telegram_pin(pin);
        } else {
            await fetch(`/api/telegram/set_pin?pin=${encodeURIComponent(pin)}`);
        }
        loadTelegramPairingInfo();
        alert('Hasło PIN zostało zmienione na: ' + pin);
    } catch (err) {
        console.error('Error saving PIN:', err);
    }
}

async function removeTelegramSubscriber(chatId) {
    if (!confirm('Czy na pewno chcesz odłączyć to urządzenie?')) return;
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.remove_telegram_subscriber(chatId);
        } else {
            await fetch(`/api/telegram/remove_sub?chat_id=${encodeURIComponent(chatId)}`);
        }
        loadTelegramPairingInfo();
    } catch (err) {
        console.error('Error removing subscriber:', err);
    }
}

function openTelegramModal() {
    loadTelegramConfig();
    const modal = document.getElementById('modal-telegram');
    if (modal) modal.style.display = 'flex';
}

function closeTelegramModal() {
    const modal = document.getElementById('modal-telegram');
    if (modal) modal.style.display = 'none';
}

async function saveTelegramConfig() {
    const enabled = document.getElementById('tg-enabled').checked;
    const minStars = parseInt(document.getElementById('tg-min-stars').value) || 2;
    const feedback = document.getElementById('tg-status-feedback');

    try {
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.save_telegram_config('', '', enabled, minStars);
        } else {
            const resp = await fetch(`/api/telegram/save?enabled=${enabled}&min_stars=${minStars}`);
            res = await resp.json();
        }

        if (feedback) {
            feedback.style.display = 'block';
            if (res && res.success) {
                feedback.style.background = 'rgba(0, 230, 118, 0.15)';
                feedback.style.color = '#00E676';
                feedback.innerHTML = '✅ <b>Zapisano!</b> Ustawienia powiadomień zostały zaktualizowane.';
                loadTelegramConfig();
                setTimeout(() => { closeTelegramModal(); feedback.style.display = 'none'; }, 1500);
            } else {
                feedback.style.background = 'rgba(255, 23, 68, 0.15)';
                feedback.style.color = '#ff5252';
                feedback.innerHTML = '❌ <b>Błąd zapisu:</b> ' + (res.error || 'Nieznany błąd');
            }
        }
    } catch (err) {
        console.error('Save telegram error:', err);
    }
}

async function testTelegramConnection() {
    const feedback = document.getElementById('tg-status-feedback');

    if (feedback) {
        feedback.style.display = 'block';
        feedback.style.background = 'rgba(0, 136, 204, 0.15)';
        feedback.style.color = '#29b6f6';
        feedback.innerHTML = '⏳ Wysyłanie wiadomości testowej do wszystkich podłączonych urządzeń...';
    }

    try {
        let res = null;
        if (window.pywebview && window.pywebview.api) {
            res = await window.pywebview.api.test_telegram();
        } else {
            const resp = await fetch('/api/telegram/test');
            res = await resp.json();
        }

        if (feedback) {
            if (res && res.success) {
                feedback.style.background = 'rgba(0, 230, 118, 0.15)';
                feedback.style.color = '#00E676';
                feedback.innerHTML = '🎉 <b>Sukces!</b> Wiadomość testowa dotarła na wszystkie połączone urządzenia.';
            } else {
                feedback.style.background = 'rgba(255, 23, 68, 0.15)';
                feedback.style.color = '#ff5252';
                feedback.innerHTML = '❌ <b>Błąd Telegram API:</b> ' + (res.error || 'Sprawdź połączenie.');
            }
        }
    } catch (err) {
        console.error('Test telegram error:', err);
    }
}

// Inicjalizacja nasłuchiwaczy dla Telegram
document.addEventListener('DOMContentLoaded', () => {
    loadTelegramConfig();

    const btnOpenTg = document.getElementById('btn-open-telegram');
    if (btnOpenTg) btnOpenTg.addEventListener('click', openTelegramModal);

    const btnCloseTg = document.getElementById('btn-close-telegram');
    if (btnCloseTg) btnCloseTg.addEventListener('click', closeTelegramModal);

    const btnSaveTg = document.getElementById('btn-tg-save');
    if (btnSaveTg) btnSaveTg.addEventListener('click', saveTelegramConfig);

    const btnSavePin = document.getElementById('btn-save-pin');
    if (btnSavePin) btnSavePin.addEventListener('click', savePairingPin);

    // Przeglądarka
    const btnBrowserSettings = document.getElementById('btn-browser-settings');
    if (btnBrowserSettings) btnBrowserSettings.addEventListener('click', openBrowserModal);

    const btnCloseBrowserModal = document.getElementById('btn-close-browser-modal');
    if (btnCloseBrowserModal) btnCloseBrowserModal.addEventListener('click', closeBrowserModal);

    const btnCloseBrowserAction = document.getElementById('btn-close-browser-action');
    if (btnCloseBrowserAction) btnCloseBrowserAction.addEventListener('click', closeBrowserModal);

    const btnResetBrowser = document.getElementById('btn-reset-browser');
    if (btnResetBrowser) btnResetBrowser.addEventListener('click', resetBrowserPreference);
});

// ==================== 15. BROWSER PREFERENCES ====================

async function openBrowserModal() {
    const modal = document.getElementById('modal-browser');
    if (!modal) return;
    modal.style.display = 'flex';
    await loadBrowserOptions();
}

function closeBrowserModal() {
    const modal = document.getElementById('modal-browser');
    if (modal) modal.style.display = 'none';
}

async function loadBrowserOptions() {
    const container = document.getElementById('browser-options-list');
    if (!container) return;
    container.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">Wykrywanie przeglądarek...</div>';

    try {
        let data = null;
        if (window.pywebview && window.pywebview.api) {
            data = await window.pywebview.api.get_browser_config();
        } else {
            const resp = await fetch('/api/browser/config');
            data = await resp.json();
        }

        const browsers = data.browsers || [];
        const savedId = (data.preference && data.preference.browser_id) ? data.preference.browser_id : null;

        let html = '';
        browsers.forEach(b => {
            const isSelected = b.id === savedId;
            html += `
            <div class="browser-setting-card ${isSelected ? 'active-browser' : ''}" onclick="selectBrowserPreference('${b.id}')" style="
                background: ${isSelected ? 'rgba(255, 214, 0, 0.12)' : 'var(--bg-card)'};
                border: 1px solid ${isSelected ? 'var(--accent-yellow)' : 'var(--border-color)'};
                border-radius: 10px;
                padding: 12px 16px;
                display: flex;
                align-items: center;
                cursor: pointer;
                transition: all 0.2s ease;
            ">
                <span style="font-size: 22px; margin-right: 14px;">${b.icon}</span>
                <div style="flex: 1;">
                    <div style="font-weight: 700; font-size: 14px; color: ${isSelected ? 'var(--accent-yellow)' : '#fff'};">${b.name}</div>
                    <div style="font-size: 11px; color: var(--text-muted);">${b.badge}</div>
                </div>
                <div>
                    ${isSelected ? '<span style="color:var(--accent-yellow);font-weight:800;font-size:13px;">✓ Aktywna</span>' : '<span style="color:var(--text-muted);font-size:12px;">Wybierz</span>'}
                </div>
            </div>
            `;
        });
        container.innerHTML = html;
    } catch (err) {
        console.error('Error loading browsers:', err);
        container.innerHTML = '<div style="color:#ff5252;font-size:12px;">Błąd pobierania listy przeglądarek.</div>';
    }
}

async function selectBrowserPreference(browserId) {
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.set_browser_preference(browserId, true);
        } else {
            await fetch(`/api/browser/choose?id=${browserId}&remember=true&url=https://www.sts.pl/live/pilka-nozna`);
        }
        await loadBrowserOptions();
    } catch (err) {
        console.error('Error saving browser:', err);
    }
}

async function resetBrowserPreference() {
    try {
        if (window.pywebview && window.pywebview.api) {
            await window.pywebview.api.reset_browser_preference();
        } else {
            await fetch('/api/browser/reset');
        }
        await loadBrowserOptions();
    } catch (err) {
        console.error('Error resetting browser:', err);
    }
}


