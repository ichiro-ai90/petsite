import urllib.request
d=urllib.request.urlopen(urllib.request.Request("https://maps.gsi.go.jp/js/muni.js",headers={"User-Agent":"petnavi-probe/1.0"}),timeout=30).read().decode("utf-8","ignore")
open("shots/muni.js","w").write(d)
