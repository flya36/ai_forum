/* 管理端页面脚本（admin.html / admin_tags.html / admin_boards.html / admin_board_applications.html，
 * CSP 收紧后外部化）。按 DOM 元素存在性分支执行：
 *  - confirm-form：删除/置顶等表单提交确认
 *  - 标签弹窗（新增/编辑/删除确认）
 *  - 板块弹窗（新增/编辑/删除确认）
 *  - 板块申请驳回弹窗
 */
(function () {
    'use strict';

    /* ===== admin.html：表单提交确认（原 onsubmit="return confirm(...)"） ===== */
    document.querySelectorAll('form.confirm-form').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            var msg = form.getAttribute('data-confirm-msg') || '确定执行该操作？';
            if (!window.confirm(msg)) e.preventDefault();
        });
    });

    /* ===== admin.html：用户封禁弹窗（点击"封禁"填写原因+时长） ===== */
    var banModalEl = document.getElementById('ban-modal');
    if (banModalEl) {
        var banForm = document.getElementById('ban-form');
        var banModalUsername = document.getElementById('ban-modal-username');
        document.querySelectorAll('.btn-ban-open').forEach(function (btn) {
            btn.addEventListener('click', function () {
                banForm.action = btn.getAttribute('data-action') || '';
                banModalUsername.textContent = btn.getAttribute('data-username') || '';
                // 重置上次输入的原因，避免误提交旧内容
                var reasonInput = banForm.querySelector('input[name="reason"]');
                if (reasonInput) reasonInput.value = '';
                var modal = new bootstrap.Modal(banModalEl);
                modal.show();
                if (reasonInput) reasonInput.focus();
            });
        });
    }

    /* ===== admin_tags.html：标签弹窗 ===== */
    function openTagModal(tagId, tagName) {
        var form = document.getElementById('tag-form');
        if (!form) return;
        var title = document.getElementById('tag-modal-title');
        var input = document.getElementById('tag-name-input');
        if (tagId) {
            form.action = '/admin/tags/' + tagId + '/edit';
            title.textContent = '编辑标签';
            input.value = tagName || '';
        } else {
            form.action = '/admin/tags/add';
            title.textContent = '新增标签';
            input.value = '';
        }
        var modal = new bootstrap.Modal(document.getElementById('tag-modal'));
        modal.show();
        input.focus();
    }
    var addTagBtn = document.getElementById('add-tag-btn');
    if (addTagBtn) addTagBtn.addEventListener('click', function () { openTagModal(); });
    document.querySelectorAll('.edit-tag-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            openTagModal(btn.getAttribute('data-tag-id'), btn.getAttribute('data-tag-name'));
        });
    });
    document.querySelectorAll('.delete-tag-form').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            var name = form.getAttribute('data-tag-name') || '';
            if (!window.confirm('确定删除标签 #' + name + '？\n若该标签已被文章使用将无法删除。')) {
                e.preventDefault();
            }
        });
    });

    /* ===== admin_boards.html：板块弹窗 ===== */
    function openBoardModal(boardId, boardName, boardDesc) {
        var form = document.getElementById('board-form');
        if (!form) return;
        var title = document.getElementById('board-modal-title');
        var nameInput = document.getElementById('board-name-input');
        var descInput = document.getElementById('board-desc-input');
        if (boardId) {
            form.action = '/admin/boards/' + boardId + '/edit';
            title.textContent = '编辑板块';
            nameInput.value = boardName || '';
            descInput.value = boardDesc || '';
        } else {
            form.action = '/admin/boards/create';
            title.textContent = '新增板块';
            nameInput.value = '';
            descInput.value = '';
        }
        var modal = new bootstrap.Modal(document.getElementById('board-modal'));
        modal.show();
        nameInput.focus();
    }
    var addBoardBtn = document.getElementById('add-board-btn');
    if (addBoardBtn) addBoardBtn.addEventListener('click', function () { openBoardModal(); });
    document.querySelectorAll('.edit-board-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            openBoardModal(
                btn.getAttribute('data-board-id'),
                btn.getAttribute('data-board-name'),
                btn.getAttribute('data-board-desc')
            );
        });
    });
    document.querySelectorAll('.delete-board-form').forEach(function (form) {
        form.addEventListener('submit', function (e) {
            var name = form.getAttribute('data-board-name') || '';
            if (!window.confirm('确定删除板块 #' + name + '？\n板块下有帖子时将无法删除。')) {
                e.preventDefault();
            }
        });
    });

    /* ===== admin_board_applications.html：驳回弹窗 ===== */
    document.querySelectorAll('.reject-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var appId = btn.getAttribute('data-app-id');
            var name = btn.getAttribute('data-app-name') || '';
            document.getElementById('reject-form').action = '/admin/board-applications/' + appId + '/reject';
            document.getElementById('reject-board-name').textContent = '板块：' + name;
            var modal = new bootstrap.Modal(document.getElementById('reject-modal'));
            modal.show();
        });
    });
})();
