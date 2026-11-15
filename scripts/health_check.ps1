param(
    [string]$BaseUrl = "http://127.0.0.1:5000",
    [int]$TimeoutSec = 10
)

$ErrorActionPreference = "Stop"
$headers = @{}
if ($env:BIOKB_API_TOKEN) {
    $headers.Authorization = "Bearer " + $env:BIOKB_API_TOKEN
}
$results = foreach ($path in @("/", "/api/health", "/api/dashboard", "/api/config", "/api/documents?limit=1")) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri ($BaseUrl.TrimEnd('/') + $path) -Headers $headers -TimeoutSec $TimeoutSec
        $ok = [int]$response.StatusCode -eq 200
        if ($path -eq "/api/health") {
            $ok = $ok -and (($response.Content | ConvertFrom-Json).status -eq "ok")
        }
        [PSCustomObject]@{ Path = $path; Status = [int]$response.StatusCode; Ok = $ok }
    }
    catch {
        # 不输出请求头、令牌或完整响应内容。
        [PSCustomObject]@{ Path = $path; Status = 0; Ok = $false }
    }
}
$results | Format-Table -AutoSize
if (@($results | Where-Object { -not $_.Ok }).Count -gt 0) {
    Write-Host "检查未通过：请确认服务地址；启用认证时需设置 BIOKB_API_TOKEN 环境变量。"
    exit 1
}
Write-Host "页面与知识库接口检查通过。本检查不代表生成模型或向量模型连接成功。"
exit 0
