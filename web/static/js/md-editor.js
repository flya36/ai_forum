/* 轻量 Markdown 编辑器：textarea + 实时预览（双栏，窄屏自动上下堆叠）
 * 预览走服务端 /api/markdown/preview（后端 markdown+bleach 白名单过滤），
 * 前端仅展示服务端返回的安全 HTML，不做任何二次解析执行。
 * 依赖：window.CSRF_TOKEN（base.html 已注入）；无第三方库。
 */
(function () {
    'use strict';
    var PREVIEW_URL = '/api/markdown/preview';
    var timers = {};

    function getCsrf() {
        return window.CSRF_TOKEN || '';
    }

    function renderPreview(textarea, preview, immediate) {
        var content = (textarea.value || '').trim();
        if (!content) {
            preview.innerHTML = '<div class="text-muted small" style="padding:8px;">预览为空，输入内容后自动渲染</div>';
            return;
        }
        if (timers[textarea.id || textarea.name]) clearTimeout(timers[textarea.id || textarea.name]);
        timers[textarea.id || textarea.name] = setTimeout(function () {
            var fd = new FormData();
            fd.append('content', textarea.value);
            fd.append('_csrf_token', getCsrf());
            fetch(PREVIEW_URL, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                body: fd
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data && typeof data.html === 'string') {
                    preview.innerHTML = data.html;   // 服务端已 bleach 过滤，可安全插入
                } else {
                    preview.innerHTML = '<div class="text-danger small">预览渲染失败</div>';
                }
            }).catch(function () {
                preview.innerHTML = '<div class="text-danger small">预览服务暂不可用（不影响提交）</div>';
            });
        }, immediate ? 0 : 400);
    }

    function initEditor(textarea) {
        if (textarea.dataset.mdEditorReady) return;
        textarea.dataset.mdEditorReady = '1';

        var wrap = document.createElement('div');
        wrap.className = 'md-editor-wrap';
        textarea.parentNode.insertBefore(wrap, textarea);
        wrap.appendChild(textarea);

        var preview = document.createElement('div');
        preview.className = 'md-preview markdown-body';
        preview.innerHTML = '<div class="text-muted small" style="padding:8px;">实时预览</div>';
        wrap.appendChild(preview);

        textarea.addEventListener('input', function () { renderPreview(textarea, preview, false); });
        // 编辑页回填已有内容：加载后立即渲染一次
        if (textarea.value.trim()) renderPreview(textarea, preview, true);

        // 移动端（≤640px）：编辑/预览 Tab 切换；桌面保持双栏
        if (window.matchMedia && window.matchMedia('(max-width: 640px)').matches) {
            initMobileTabs(textarea, preview);
        }
    }

    function initMobileTabs(textarea, preview) {
        var wrap = textarea.parentNode;
        if (!wrap) return;
        var tabs = document.createElement('div');
        tabs.className = 'md-editor-tabs';
        var btnEdit = document.createElement('button');
        btnEdit.type = 'button';
        btnEdit.className = 'active';
        btnEdit.textContent = '编辑';
        var btnPrev = document.createElement('button');
        btnPrev.type = 'button';
        btnPrev.textContent = '预览';
        tabs.appendChild(btnEdit);
        tabs.appendChild(btnPrev);
        wrap.parentNode.insertBefore(tabs, wrap);

        function showEdit() {
            btnEdit.classList.add('active');
            btnPrev.classList.remove('active');
            textarea.style.display = '';
            preview.style.display = 'none';
        }
        function showPreview() {
            btnPrev.classList.add('active');
            btnEdit.classList.remove('active');
            textarea.style.display = 'none';
            preview.style.display = '';
            renderPreview(textarea, preview, true);
        }
        btnEdit.addEventListener('click', showEdit);
        btnPrev.addEventListener('click', showPreview);
        showEdit();
    }

    function initAll() {
        var list = document.querySelectorAll('textarea.md-editor');
        for (var i = 0; i < list.length; i++) initEditor(list[i]);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAll);
    } else {
        initAll();
    }
})();
