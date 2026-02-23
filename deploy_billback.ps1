$AWSPROFILE='jrk-analytics-admin'
$REGION='us-east-1'
$SRC='H:\Business_Intelligence\1. COMPLETED_PROJECTS\WINDSURF_DEV\bill_review_app'
$ZIP="$SRC\..\bill_review_app_src.zip"

Write-Host "Building and deploying bill review app..."

if (Test-Path $ZIP) {
    Remove-Item $ZIP -Force
    Write-Host "Removed existing zip"
}

Compress-Archive -Path "$SRC\*" -DestinationPath $ZIP -Force
Write-Host "Created source zip"

aws s3 cp $ZIP s3://jrk-analytics-billing/tmp/jrk-bill-review/source.zip --region $REGION --profile $AWSPROFILE
Write-Host "Uploaded to S3"

$buildId = aws codebuild start-build --project-name jrk-bill-review-build --region $REGION --profile $AWSPROFILE --query 'build.id' --output text
Write-Host "Started CodeBuild: $buildId"
Write-Output $buildId
