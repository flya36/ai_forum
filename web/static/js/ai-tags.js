// AI 标签推荐（new_post.html，CSP 兼容：外部 JS，无内联）
// 点击"AI 推荐标签"→ 提取标题/正文 → POST /api/ai/recommend_tags（带 CSRF）
// → 渲染推荐标签，点击即加入已选（复用 window.addTag，合并不覆盖）
(function () {
    'use strict';
    var btn = document.getElementById('ai-recommend-btn');
    if (!btn || !window.fetch) return;

    var box = document.getElementById('ai-recommend-tags');
    var status = document.getElementById('ai-recommend-status');
    var titleEl = document.querySelector('input[name="title"]');
    var contentEl = document.querySelector('textarea[name="content"]');

    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    btn.addEventListener('click', function () {
        var title = titleEl ? titleEl.value.trim() : '';
        var content = contentEl ? contentEl.value.trim() : '';
        if (!title && !content) {
            if (status) { status.textContent = '请先输入标题或内容'; }
            return;
        }
        btn.disabled = true;
        if (status) { status.textContent = 'AI 分析中…'; }
        var fd = new FormData();
        fd.append('_csrf_token', csrfToken());
        fd.append('title', title);
        fd.append('content', content);
        fetch('/api/ai/recommend_tags', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: fd
        }).then(function (r) {
            if (r.status === 429) {
                if (status) { status.textContent = '调用过于频繁，请稍后再试'; }
                return null;
            }
            return r.json();
        }).then(function (d) {
            if (!d) { btn.disabled = false; return; }
            btn.disabled = false;
            if (!d.ok) {
                if (status) { status.textContent = d.msg || 'AI 推荐暂时不可用'; }
                return;
            }
            box.innerHTML = '';
            if (!d.tags || d.tags.length === 0) {
                if (status) { status.textContent = 'AI 推荐暂时不可用，请稍后再试'; }
                return;
            }
            if (status) { status.textContent = ''; }
            d.tags.forEach(function (name) {
                var chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'btn btn-sm btn-outline-info ai-tag';
                chip.textContent = '+ # ' + name;   // textContent 防注入
                chip.addEventListener('click', function () {
                    if (window.addTag) {
                        window.addTag(name);
                        chip.textContent = '# ' + name;
                        chip.classList.remove('btn-outline-info');
                        chip.classList.add('btn-info');
                        chip.disabled = true;
                    }
                });
                box.appendChild(chip);
            });
        }).catch(function () {
            btn.disabled = false;
            if (status) { status.textContent = 'AI 推荐暂时不可用，请稍后再试'; }
        });
    });
})();
