// 用户主页：关注/取关 AJAX（CSP 兼容：外部 JS，无内联）
// CSRF token 取自 base.html 的 <meta name="csrf-token">
(function () {
    'use strict';
    var btn = document.getElementById('follow-btn');
    if (!btn || !window.fetch) return;

    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    btn.addEventListener('click', function () {
        // 按钮状态决定动作：未关注 -> follow；已关注 -> unfollow
        var isFollowing = btn.getAttribute('data-state') === 'following';
        var fd = new FormData();
        fd.append('_csrf_token', csrfToken());
        fd.append('action', isFollowing ? 'unfollow' : 'follow');
        fetch('/user/' + btn.getAttribute('data-target-id') + '/follow', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: fd
        }).then(function (r) {
            if (r.status === 429) { alert('操作过于频繁，请稍后再试'); return null; }
            return r.json();
        }).then(function (d) {
            if (!d) return;
            if (!d.ok) { alert(d.msg || '操作失败'); return; }
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
            // 即时更新关注数/粉丝数（按钮所属用户主页的计数）
            var fc = document.getElementById('following-count');
            var fr = document.getElementById('follower-count');
            if (fc && typeof d.following_count === 'number') fc.textContent = d.following_count;
            if (fr && typeof d.follower_count === 'number') fr.textContent = d.follower_count;
        }).catch(function () {
            alert('网络错误，请稍后重试');
        });
    });
})();


// 拉黑 / 解除拉黑 AJAX（CSP 兼容：外部 JS，无内联）
(function () {
    'use strict';
    var btn = document.getElementById('block-btn');
    if (!btn || !window.fetch) return;

    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    btn.addEventListener('click', function () {
        var isBlocked = btn.getAttribute('data-state') === 'blocked';
        var fd = new FormData();
        fd.append('_csrf_token', csrfToken());
        var url = '/user/' + btn.getAttribute('data-target-id') + '/' + (isBlocked ? 'unblock' : 'block');
        fetch(url, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: fd
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d) return;
            if (!d.ok) { alert(d.msg || '操作失败'); return; }
            if (isBlocked) {
                btn.textContent = '拉黑';
                btn.setAttribute('data-state', 'not');
                btn.classList.remove('btn-outline-secondary');
                btn.classList.add('btn-outline-danger');
            } else {
                btn.textContent = '已拉黑（点击解除）';
                btn.setAttribute('data-state', 'blocked');
                btn.classList.remove('btn-outline-danger');
                btn.classList.add('btn-outline-secondary');
            }
        }).catch(function () {
            alert('网络错误，请稍后重试');
        });
    });
})();
