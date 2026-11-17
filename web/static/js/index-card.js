/* ============================================================
 * 首页帖子卡片：关注/取关按钮 AJAX（CSP 兼容，外部 JS，无内联）
 * 复用 /user/<id>/follow 接口（CSRF 全局校验，meta[name=csrf-token]）
 * 事件委托：兼容 index-list.js 动态插入的新卡片
 * ============================================================ */
(function () {
    'use strict';

    function getCsrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    document.addEventListener('click', function (ev) {
        var btn = ev.target.closest ? ev.target.closest('.btn-follow-post') : null;
        if (!btn) return;
        ev.preventDefault();
        ev.stopPropagation();

        var targetId = btn.getAttribute('data-target-id');
        if (!targetId) return;
        var isFollowing = btn.getAttribute('data-state') === 'following';

        var fd = new FormData();
        fd.append('_csrf_token', getCsrf());
        fd.append('action', isFollowing ? 'unfollow' : 'follow');

        fetch('/user/' + targetId + '/follow', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: fd
        })
        .then(function (resp) {
            if (!resp.ok) return resp.json().then(function (d) { throw new Error(d.msg || '操作失败'); });
            return resp.json();
        })
        .then(function (d) {
            if (!d.ok) {
                alert(d.msg || '操作失败');
                return;
            }
            if (d.action === 'follow') {
                btn.textContent = '已关注';
                btn.setAttribute('data-state', 'following');
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-secondary');
            } else if (d.action === 'unfollow') {
                btn.textContent = '关注';
                btn.setAttribute('data-state', 'not');
                btn.classList.remove('btn-secondary');
                btn.classList.add('btn-primary');
            }
        })
        .catch(function (err) {
            alert(err.message || '操作失败，请稍后再试');
        });
    });
})();
