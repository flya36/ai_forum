/* 标签编辑器（new_post.html / edit_post.html 共用，CSP 收紧后外部化）
 * 选择已有标签 + 输入新标签（最多5个），写入隐藏域 tags-input（JSON 数组）
 * 编辑页初始标签通过 <input type="hidden" id="initial-tags" value='...tojson...'> 注入。
 */
(function () {
    'use strict';
    var MAX = 5;
    var hidden = document.getElementById('tags-input');
    if (!hidden) return;   // 非发帖/编辑页
    var selected = [];
    var box = document.getElementById('selected-tags');
    var newInput = document.getElementById('new-tag-input');
    var suggestBox = document.getElementById('tag-suggest');
    var suggestTimer = null;

    function syncHidden() {
        // JSON 数组序列化：标签名可含逗号，不能用逗号拼接（后端按 JSON 解析）
        hidden.value = JSON.stringify(selected);
    }

    function renderSelected() {
        box.innerHTML = '';
        if (selected.length === 0) {
            var empty = document.createElement('span');
            empty.className = 'text-muted small';
            empty.textContent = '尚未选择标签';
            box.appendChild(empty);
            return;
        }
        selected.forEach(function (name) {
            var chip = document.createElement('span');
            chip.className = 'badge bg-primary d-inline-flex align-items-center';
            chip.style.fontSize = '13px';
            var text = document.createElement('span');
            text.textContent = '# ' + name;          // textContent 防注入
            var close = document.createElement('a');
            close.href = 'javascript:void(0)';
            close.className = 'ms-1 text-white';
            close.style.textDecoration = 'none';
            close.textContent = '×';
            close.addEventListener('click', function () { removeTag(name); });
            chip.appendChild(text);
            chip.appendChild(close);
            box.appendChild(chip);
        });
    }

    function syncExistingButtons() {
        document.querySelectorAll('.existing-tag').forEach(function (b) {
            var name = b.getAttribute('data-name');
            if (selected.indexOf(name) !== -1) {
                b.classList.remove('btn-outline-secondary');
                b.classList.add('btn-primary');
            } else {
                b.classList.add('btn-outline-secondary');
                b.classList.remove('btn-primary');
            }
        });
    }

    function addTag(name) {
        name = (name || '').trim();
        if (!name) return false;
        if (selected.indexOf(name) !== -1) return false;
        if (selected.length >= MAX) {
            alert('最多选择 ' + MAX + ' 个标签');
            return false;
        }
        selected.push(name);
        syncHidden();
        renderSelected();
        syncExistingButtons();
        return true;
    }

    function removeTag(name) {
        selected = selected.filter(function (x) { return x !== name; });
        syncHidden();
        renderSelected();
        syncExistingButtons();
    }

    window.toggleExisting = function (btn) {
        var name = btn.getAttribute('data-name');
        if (selected.indexOf(name) !== -1) {
            removeTag(name);
        } else if (!addTag(name)) {
            alert('最多选择 ' + MAX + ' 个标签');
        }
    };

    window.addNewTag = function () {
        var name = newInput.value.trim();
        if (!name) return;
        if (selected.indexOf(name) !== -1) {
            alert('该标签已添加，请勿重复选择');
            return;
        }
        if (selected.length >= MAX) {
            alert('最多选择 ' + MAX + ' 个标签');
            return;
        }
        if (addTag(name)) {
            newInput.value = '';
            hideSuggest();
        }
    };

    // AI 推荐标签复用：点击推荐标签即加入已选（合并，不覆盖已选）
    window.addTag = addTag;

    /* ===== 事件绑定（原内联 onclick 外部化） ===== */
    var addBtn = document.querySelector('#new-tag-input + button, .input-group button');
    if (addBtn) addBtn.addEventListener('click', function () { window.addNewTag(); });
    document.querySelectorAll('.existing-tag').forEach(function (btn) {
        btn.addEventListener('click', function () { window.toggleExisting(btn); });
    });

    newInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            window.addNewTag();
        }
    });

    newInput.addEventListener('input', function () {
        clearTimeout(suggestTimer);
        var q = newInput.value.trim();
        if (!q) { hideSuggest(); return; }
        suggestTimer = setTimeout(function () {
            fetch('/api/tags/search?q=' + encodeURIComponent(q))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data.results || data.results.length === 0) { hideSuggest(); return; }
                    suggestBox.innerHTML = '';
                    data.results.forEach(function (t) {
                        if (selected.indexOf(t.tag_name) !== -1) return;  // 已选的不提示
                        var item = document.createElement('a');
                        item.href = 'javascript:void(0)';
                        item.className = 'list-group-item list-group-item-action py-1 px-3';
                        item.textContent = '# ' + t.tag_name;   // textContent 防注入
                        item.addEventListener('click', function () {
                            newInput.value = '';
                            if (addTag(t.tag_name)) hideSuggest();
                        });
                        suggestBox.appendChild(item);
                    });
                    suggestBox.style.display = suggestBox.children.length ? 'block' : 'none';
                }).catch(function () { hideSuggest(); });
        }, 200);
    });

    function hideSuggest() { suggestBox.style.display = 'none'; }
    document.addEventListener('click', function (e) {
        if (!e.target.closest('#new-tag-input') && !e.target.closest('#tag-suggest')) hideSuggest();
    });

    /* ===== 编辑模式初始化：回填帖子已有标签 ===== */
    var initialEl = document.getElementById('initial-tags');
    if (initialEl && initialEl.value) {
        try {
            var initialNames = JSON.parse(initialEl.value);
            if (Array.isArray(initialNames)) {
                initialNames.forEach(function (name) {
                    if (selected.indexOf(name) === -1) selected.push(name);
                });
                syncHidden();
                renderSelected();
                syncExistingButtons();
            }
        } catch (e) { /* 解析失败按空处理，不阻塞页面 */ }
    }
})();
