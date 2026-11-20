# 第三方来源与许可

天工录自己的代码采用 MIT。以下数据及构建/运行依赖保留各自许可，不因项目许可而更换。

## 地图

`src/web/assets/travel-places.json` 由地理数据转换为界面使用的简化轮廓。

- 中国省级轮廓：[Supeset/China-GeoData](https://github.com/Supeset/China-GeoData)，固定版本 `5822c4c0a0bdfd73327f9454976c8661bfd6ad9f`。原数据 MIT License，Copyright (c) 2025 圈集；完整许可见下方及数据文件的来源信息。
- 世界轮廓：Natural Earth 5.1.2，Made with Natural Earth。[官方使用条款](https://www.naturalearthdata.com/about/terms-of-use/)将其栅格和矢量地图数据置于公有领域。

地图仅用于记录旅行足迹，不作为测绘、法律或边界判断依据。

## 营养数据

`data/food-nutrients-v1.json` 包含 44 项食材映射，来源为 [USDA National Nutrient Database SR28 (2015)](https://www.ars.usda.gov/ARSUserFiles/80400525/Data/SR/SR28/dnload/sr28asc.zip)。来源文件 SHA-256、每 100g 的定义与估算假设保留在数据文件中，转换工具为 `scripts/import_nutrition_sr28.py`。

数据说明参见 [USDA FoodData Central 文档](https://fdc.nal.usda.gov/data-documentation.html)。食材别名、可食比例和家庭单位转换是本项目的近似映射；不表示 USDA 对具体菜谱或健康建议的认可。

## 依赖

- Terser：BSD-2-Clause，开发时压缩 JavaScript；许可保留在 npm 包中。
- Pillow：MIT-CMU，图像处理；许可随 Python 包提供。
- openpyxl：MIT，表格读取；许可随 Python 包提供。
- openpyxl 的传递依赖 et-xmlfile：MIT，许可随 Python 包提供。

版本锁定在 `package-lock.json` 与 `requirements.txt`。公开仓库不捆绑依赖源码、系统字体、角色插画或原生客户端的图标包。浏览器使用部署设备提供的系统字体；新笔记本图标为本项目原创。

## China-GeoData 的 MIT 许可

MIT License

Copyright (c) 2025 圈集

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
