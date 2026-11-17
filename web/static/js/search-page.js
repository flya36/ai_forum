/* 搜索结果页：AI 总结 + 关键词/语义搜索模式切换（search.html，CSP 收紧后外部化）
 * 搜索词通过 <div id="search-meta" data-query="..."> 注入。 */
(function () {
    'use strict';
    var meta = document.getElementById('search-meta');
    var query = meta ? (meta.getAttribute('data-query') || '') : '';

    /* ---- AI 总结 ---- */
    var btn = document.getElementById('ai-btn');
    if (btn) {
        btn.addEventListener('click', function () {
            var result = document.getElementById('ai-result');
            var text = document.getElementById('ai-text');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 总结中...';
            result.style.display = 'block';
            text.textContent = '正在分析相关帖子内容，请稍候...';

            fetch('/api/ask?question=' + encodeURIComponent(query))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    text.textContent = data.answer || 'AI 暂未生成总结。';
                    btn.disabled = false;
                    btn.innerHTML = '<i class="bi bi-robot"></i> AI 总结';
                })
                .catch(function (err) {
                    text.textContent = '总结失败：' + err;
                    btn.disabled = false;
                    btn.innerHTML = '<i class="bi bi-robot"></i> AI 总结';
                });
        });
    }

    /* ---- 关键词 / 语义模式切换 ---- */
    var tabs = document.querySelectorAll('#search-mode-tab .nav-link');
    var hint = document.getElementById('semantic-hint');
    if (!tabs.length) return;

    function renderSemantic(results, mode) {
        var wrap = document.getElementById('search-results');
        var noRes = document.getElementById('no-results');
        if (!wrap) return;
        if (!results || !results.length) {
            wrap.innerHTML = '<div class="content-card text-center text-muted py-5">' +
                '<i class="bi bi-search" style="font-size:48px;"></i>' +
                '<p class="mt-3">未找到与「' + escapeHtml(query) + '」相关的帖子</p>' +
                '</div>';
            return;
        }
        var html = '';
        results.forEach(function (p) {
            html += '<div class="post-card">';
            html += '<div><a href="/post/' + p.post_id + '" class="post-title">' + escapeHtml(p.title) + '</a></div>';
            html += '<div class="post-meta mt-1"><i class="bi bi-person-circle"></i> ' +
                escapeHtml(p.author || '') + ' · ' + escapeHtml(p.create_time || '') +
                ' · <i class="bi bi-eye"></i> ' + (p.view_count || 0) +
                ' · <i class="bi bi-chat"></i> ' + (p.reply_count || 0);
            if (p.similarity != null) {
                html += ' · <span class="text-success">相似度 ' + (p.similarity * 100).toFixed(1) + '%</span>';
            }
            html += '</div></div>';
        });
        wrap.innerHTML = html;
        if (noRes) noRes.style.display = 'none';
    }

    function escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    tabs.forEach(function (tab) {
        tab.addEventListener('click', function () {
            tabs.forEach(function (t) { t.classList.remove('active'); });
            tab.classList.add('active');
            var mode = tab.getAttribute('data-mode');
            if (mode !== 'semantic') {
                if (hint) hint.style.display = 'none';
                window.location.href = '/search?q=' + encodeURIComponent(query);
                return;
            }
            if (!query) { return; }
            if (hint) hint.style.display = 'block';
            var wrap = document.getElementById('search-results');
            if (wrap) {
                wrap.innerHTML = '<div class="content-card text-center text-muted py-5">' +
                    '<span class="spinner-border spinner-border-sm"></span> 正在语义匹配...</div>';
            }
            fetch('/api/search/semantic?q=' + encodeURIComponent(query))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (hint) {
                        hint.textContent = data.mode === 'semantic'
                            ? '按语义匹配相关帖子，最多返回 10 条。'
                            : '无向量索引或关键词过短，已降级为关键词搜索。';
                    }
                    renderSemantic(data.results, data.mode);
                })
                .catch(function (err) {
                    if (hint) hint.textContent = '语义搜索失败，已降级为关键词搜索。';
                    window.location.href = '/search?q=' + encodeURIComponent(query);
                });
        });
    });
})();
