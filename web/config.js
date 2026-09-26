'use strict';

/* 面板与后端分离部署时的接口地址配置。

   同源部署（默认，由 main.py 同时提供页面与接口）保持为空字符串；
   把 web/ 目录放到别的静态服务器时，这里填写后端地址，例如：

       window.ROOMSENSE_CONFIG = { apiBase: 'http://192.168.1.20:20500' };

   跨域访问需要后端 conf/config.json 的 server.cors_origins 包含面板来源，例如
   ["http://192.168.1.20:5173"]。 */
window.ROOMSENSE_CONFIG = {
  apiBase: '',
};
