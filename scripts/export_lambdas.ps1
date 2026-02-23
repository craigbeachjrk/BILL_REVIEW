param(
  [string[]]$Regions = @('us-east-1'),
  [string]$Profile = 'default',
  [string]$OutRoot = (Join-Path (Resolve-Path '..').Path 'bill_review_app/aws_lambdas')
)

function Ensure-Dir($p){ if(-not (Test-Path $p)){ New-Item -ItemType Directory -Force -Path $p | Out-Null } }

function Save-Json($obj, $path){ Ensure-Dir (Split-Path $path -Parent); $obj | ConvertTo-Json -Depth 100 | Out-File -FilePath $path -Encoding utf8 }

function Invoke-AwsCli([string[]]$CliArgs){
  $out = & aws @CliArgs 2>&1
  $code = $LASTEXITCODE
  if($code -ne 0){ throw "aws $($CliArgs -join ' ') failed: $out" }
  if([string]::IsNullOrWhiteSpace($out)){ return $null }
  try { return ($out | ConvertFrom-Json) } catch { return $null }
}

Ensure-Dir $OutRoot

foreach($region in $Regions){
  $regionRoot = Join-Path $OutRoot $region
  Ensure-Dir $regionRoot

  Write-Host "Listing Lambda functions in $region ..."
  $marker = $null
  $functions = @()
  do{
    $cliArgs = @('lambda','list-functions','--region', $region, '--profile', $Profile)
    if($marker){ $cliArgs += @('--marker', $marker) }
    $resp = Invoke-AwsCli $cliArgs
    if($resp.Functions){ $functions += $resp.Functions }
    $marker = $resp.NextMarker
  } while($marker)

  foreach($fn in $functions){
    $name = $fn.FunctionName
    $fnRoot = Join-Path $regionRoot $name
    $codeDir = Join-Path $fnRoot 'code'
    Ensure-Dir $codeDir

    Write-Host "Exporting $name ($region) ..."
    # Configuration
    $cfg = Invoke-AwsCli @('lambda','get-function-configuration','--region', $region,'--function-name',$name,'--profile',$Profile)
    Save-Json $cfg (Join-Path $fnRoot 'function.json')

    # Tags
    try{ $tags = Invoke-AwsCli @('lambda','list-tags','--resource', $cfg.FunctionArn,'--region',$region,'--profile',$Profile); Save-Json $tags (Join-Path $fnRoot 'tags.json') } catch { }

    # Policy
    try{ $pol = Invoke-AwsCli @('lambda','get-policy','--function-name',$name,'--region',$region,'--profile',$Profile); Save-Json $pol (Join-Path $fnRoot 'policy.json') } catch { }

    # URL config
    try{ $url = Invoke-AwsCli @('lambda','get-function-url-config','--function-name',$name,'--region',$region,'--profile',$Profile); Save-Json $url (Join-Path $fnRoot 'url.json') } catch { }

    if(($cfg.PackageType -eq 'Image') -or $cfg.ImageConfigResponse){
      # Image-based lambda: save image metadata only
      $imageUri = $null
      if($cfg.PSObject.Properties.Name -contains 'Code'){
        $codeObj = $cfg.Code
        if($codeObj -and ($codeObj.PSObject.Properties.Name -contains 'ImageUri')){ $imageUri = $codeObj.ImageUri }
      }
      $img = [ordered]@{ PackageType=$cfg.PackageType; ImageUri=$imageUri; ImageConfig=$cfg.ImageConfigResponse }
      Save-Json $img (Join-Path $fnRoot 'image.json')
      continue
    }

    # Zip-based lambda: download code.zip and expand
    $gf = Invoke-AwsCli @('lambda','get-function','--function-name',$name,'--region',$region,'--profile',$Profile)
    $codeUrl = $gf.Code.Location
    if($codeUrl){
      $zipPath = Join-Path $fnRoot 'code.zip'
      Invoke-WebRequest -Uri $codeUrl -OutFile $zipPath -UseBasicParsing
      # clean previous expansion
      if(Test-Path $codeDir){ Remove-Item -Recurse -Force $codeDir }
      Ensure-Dir $codeDir
      try{ Expand-Archive -Path $zipPath -DestinationPath $codeDir -Force } catch { Write-Warning "Expand-Archive failed: $_" }
    }
  }
}

Write-Host "Export complete. Output root: $OutRoot"
