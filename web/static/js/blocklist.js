// 黑名单管理：解除拉黑 AJAX（CSP 兼容外部 JS）
(function () {
    'use strict';
    var btns = document.querySelectorAll('.unblock-btn');
    if (!btns.length || !window.fetch) return;

    function csrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute('content') : '';
    }

    Array.prototype.forEach.call(btns, function (btn) {
        btn.addEventListener('click', function () {
            var fd = new FormData();
            fd.append('_csrf_token', csrfToken());
            fetch('/user/' + btn.getAttribute('data-target-id') + '/unblock', {
                method: 'POST',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                body: fd
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (!d) return;
                if (!d.ok) { alert(d.msg || '操作失败'); return; }
                var li = btn.closest('li');
                if (li) li.remove();
                var remain = document.querySelectorAll('.unblock-btn').length;
                if (remain === 0) {
                    var empty = document.createElement('p');
                    empty.className = 'text-muted small mb-0';
                    empty.textContent = '暂无拉黑用户';
                    var card = btn.closest('.content-card');
                    if (card) {
                        var ul = card.querySelector('ul');
                        if (ul) ul.remove();
                        card.appendChild(empty);
                    }
                }
            }).catch(function () {
                alert('网络错误，请稍后重试');
            });
        });
    });
})();
