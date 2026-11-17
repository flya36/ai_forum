// 消息中心：Tab 点击激活状态（链接式切换，URL 驱动，CSP 兼容外部 JS）
(function () {
    'use strict';
    var tabs = document.querySelectorAll('#msg-center-tabs [data-msg-tab]');
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].addEventListener('click', function () {
            for (var j = 0; j < tabs.length; j++) {
                tabs[j].classList.remove('active');
            }
            this.classList.add('active');
        });
    }
})();
