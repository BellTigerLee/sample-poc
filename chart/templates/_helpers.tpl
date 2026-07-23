{{/*
Chart name (used in labels).
*/}}
{{- define "sample-poc.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified resource name. Federation passes releaseName=scalex-sample-poc,
so resources become scalex-sample-poc by default.
*/}}
{{- define "sample-poc.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Chart name-version label value.
*/}}
{{- define "sample-poc.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "sample-poc.labels" -}}
helm.sh/chart: {{ include "sample-poc.chart" . }}
{{ include "sample-poc.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: sample-poc
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "sample-poc.selectorLabels" -}}
app.kubernetes.io/name: {{ include "sample-poc.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Image reference. Federation injects .digest into the values; until then the
tag-only form is rendered for local development.
*/}}
{{- define "sample-poc.image" -}}
{{- if .digest -}}
{{- printf "%s:%s@%s" .repository .tag .digest -}}
{{- else -}}
{{- printf "%s:%s" .repository .tag -}}
{{- end -}}
{{- end }}
