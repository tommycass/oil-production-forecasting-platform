{
  "title": "INFRASTRUCTURE MONITORING",
  "uid": "verified-infra-dash",
  "tags": ["api", "fastapi", "prometheus", "aws"],
  "timezone": "utc",
  "version": 106,
  "schemaVersion": 40,
  "refresh": "10s",
  "templating": {
    "list": [
      {
        "name": "instance",
        "label": "Instancia EC2",
        "type": "custom",
        "hide": 2,
        "query": "__EC2_INSTANCE_ID__",
        "current": {"text": "__EC2_INSTANCE_ID__", "value": "__EC2_INSTANCE_ID__"},
        "options": [
          {"text": "__EC2_INSTANCE_ID__", "value": "__EC2_INSTANCE_ID__", "selected": true}
        ],
        "includeAll": false,
        "multi": false,
        "refresh": 0
      },
      {
        "name": "container",
        "label": "Contenedor",
        "type": "query",
        "datasource": {"type": "prometheus"},
        "query": "label_values(container_cpu_usage_seconds_total{name!=\"\"}, name)",
        "refresh": 2,
        "includeAll": true,
        "allValue": ".+",
        "multi": true,
        "sort": 1
      },
      {
        "name": "endpoint",
        "label": "Endpoint",
        "type": "query",
        "datasource": {"type": "prometheus"},
        "query": "label_values(http_request_duration_seconds_bucket, handler)",
        "refresh": 2,
        "includeAll": true,
        "allValue": ".+",
        "multi": true,
        "sort": 1
      },
      {
        "name": "interval",
        "label": "Ventana de cálculo",
        "type": "custom",
        "query": "1m,5m,15m,1h",
        "current": {"selected": true, "text": "5m", "value": "5m"},
        "options": [
          {"selected": false, "text": "1m", "value": "1m"},
          {"selected": true, "text": "5m", "value": "5m"},
          {"selected": false, "text": "15m", "value": "15m"},
          {"selected": false, "text": "1h", "value": "1h"}
        ],
        "includeAll": false,
        "multi": false
      }
    ]
  },
  "panels": [
    {
      "id": 200,
      "type": "row",
      "title": "Overview - SLOs y KPIs",
      "collapsed": false,
      "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1},
      "panels": []
    },
    {
      "id": 1,
      "type": "stat",
      "title": "Uptime",
      "description": "Disponibilidad del servicio API",
      "targets": [{"expr": "up{job=\"api\"}", "legendFormat": "Estado", "refId": "A"}],
      "gridPos": {"x": 0, "y": 1, "w": 6, "h": 6},
      "options": {
        "colorMode": "background",
        "justifyMode": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false},
        "textMode": "value",
        "graphMode": "none"
      },
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"options": {"0": {"text": "DOWN"}}, "type": "value"},
            {"options": {"1": {"text": "UP"}}, "type": "value"}
          ],
          "thresholds": {
            "mode": "absolute",
            "steps": [{"color": "red", "value": null}, {"color": "green", "value": 1}]
          }
        }
      }
    },
    {
      "id": 2,
      "type": "gauge",
      "title": "Tasa de Errores (5xx)",
      "description": "Porcentaje de peticiones fallidas",
      "targets": [{"expr": "(sum(rate(http_requests_total{status=~\"5..\"}[5m])) or vector(0)) / (sum(rate(http_requests_total[5m])) or vector(1)) * 100", "legendFormat": "% Errores", "refId": "A"}],
      "gridPos": {"x": 6, "y": 1, "w": 6, "h": 6},
      "options": {
        "reduceOptions": {"calcs": ["lastNotNull"]},
        "showThresholdLabels": false,
        "showThresholdMarkers": true
      },
      "fieldConfig": {
        "defaults": {
          "min": 0,
          "max": 100,
          "unit": "percent",
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 2}, {"color": "red", "value": 5}]}
        }
      }
    },
    {
      "id": 5,
      "type": "stat",
      "title": "Peticiones Históricas",
      "description": "Total de peticiones en el rango del dashboard, acumulando a través de reinicios de la API",
      "targets": [{"expr": "round(sum(increase(http_requests_total[$__range])))", "legendFormat": "Reqs", "refId": "A"}],
      "gridPos": {"x": 12, "y": 1, "w": 6, "h": 6},
      "options": {
        "colorMode": "value",
        "reduceOptions": {"calcs": ["lastNotNull"]}
      },
      "fieldConfig": {
        "defaults": {
          "unit": "short",
          "decimals": 0,
          "color": {"mode": "fixed", "fixedColor": "super-light-blue"}
        }
      }
    },
    {
      "id": 6,
      "type": "gauge",
      "title": "Disponibilidad (%)",
      "description": "Porcentaje del tiempo que la API respondió al scrape de Prometheus en el rango visible",
      "targets": [{"expr": "avg_over_time(up{job=\"api\"}[$__range]) * 100", "legendFormat": "% Disponibilidad", "refId": "A"}],
      "gridPos": {"x": 18, "y": 1, "w": 6, "h": 6},
      "options": {
        "reduceOptions": {"calcs": ["lastNotNull"]},
        "showThresholdLabels": false,
        "showThresholdMarkers": true
      },
      "fieldConfig": {
        "defaults": {
          "min": 0,
          "max": 100,
          "unit": "percent",
          "decimals": 2,
          "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": null}, {"color": "orange", "value": 99}, {"color": "green", "value": 99.9}]}
        }
      }
    },
    {
      "id": 3,
      "type": "timeseries",
      "title": "Volumen de Consultas",
      "description": "Tendencia de peticiones por minuto",
      "targets": [{"expr": "sum(rate(http_requests_total[$interval])) * 60", "legendFormat": "Rendimiento (req/min)", "refId": "A"}],
      "gridPos": {"x": 0, "y": 7, "w": 24, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {
        "defaults": {
          "color": {"mode": "palette-classic"},
          "custom": {"drawStyle": "line", "fillOpacity": 20, "gradientMode": "opacity", "lineWidth": 2, "spanNulls": false}
        }
      }
    },
    {
      "id": 201,
      "type": "row",
      "title": "Performance de la API",
      "collapsed": false,
      "gridPos": {"x": 0, "y": 15, "w": 24, "h": 1},
      "panels": []
    },
    {
      "id": 4,
      "type": "timeseries",
      "title": "Tiempo de Respuesta (Latencia p95)",
      "description": "El 95% de las peticiones se completan por debajo de este tiempo",
      "targets": [{"expr": "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[$interval])) by (le))", "legendFormat": "p95 Latencia", "refId": "A"}],
      "gridPos": {"x": 0, "y": 16, "w": 24, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {
        "defaults": {
          "unit": "s",
          "color": {"mode": "fixed", "fixedColor": "orange"},
          "custom": {"drawStyle": "line", "fillOpacity": 25, "gradientMode": "opacity", "lineWidth": 2, "spanNulls": true},
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}, {"color": "red", "value": 2}]}
        }
      }
    },
    {
      "id": 13,
      "type": "state-timeline",
      "title": "Registro Histórico de Caídas (API Status)",
      "targets": [{"expr": "up{job=\"api\"}", "legendFormat": "Estado de la API", "refId": "A"}],
      "gridPos": {"x": 0, "y": 24, "w": 24, "h": 6},
      "options": {"showValue": "never", "alignValue": "left", "rowHeight": 0.9, "mergeValues": true},
      "fieldConfig": {
        "defaults": {
          "custom": {"lineWidth": 0, "fillOpacity": 80},
          "color": {"mode": "thresholds"},
          "mappings": [
            {"options": {"0": {"color": "red", "text": "Caída (DOWN)"}}, "type": "value"},
            {"options": {"1": {"color": "green", "text": "En Línea (UP)"}}, "type": "value"}
          ],
          "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": null}, {"color": "green", "value": 1}]}
        }
      }
    },
    {
      "id": 14,
      "type": "timeseries",
      "title": "Latencia p95 por Endpoint",
      "targets": [{"expr": "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{handler=~\"$endpoint\"}[$interval])) by (le, handler))", "legendFormat": "{{handler}}", "refId": "A"}],
      "gridPos": {"x": 0, "y": 30, "w": 24, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {
        "defaults": {
          "unit": "s",
          "color": {"mode": "palette-classic"},
          "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2, "spanNulls": true},
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}, {"color": "red", "value": 2}]}
        }
      }
    },
    {
      "id": 202,
      "type": "row",
      "title": "Recursos de Contenedores",
      "collapsed": false,
      "gridPos": {"x": 0, "y": 38, "w": 24, "h": 1},
      "panels": []
    },
    {
      "id": 20,
      "type": "stat",
      "title": "CPU Total Contenedores (Ahora)",
      "description": "CPU agregado de los contenedores seleccionados, como % de los cores del host",
      "targets": [{"expr": "sum(rate(container_cpu_usage_seconds_total{name!=\"\",name=~\"$container\"}[$interval])) / scalar(sum(machine_cpu_cores)) * 100", "legendFormat": "CPU", "refId": "A"}],
      "gridPos": {"x": 0, "y": 39, "w": 8, "h": 3},
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "textMode": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"]}
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percent",
          "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}
        }
      }
    },
    {
      "id": 21,
      "type": "stat",
      "title": "Memoria Total Contenedores (Ahora)",
      "description": "Memoria working set agregada como % de la memoria del host",
      "targets": [{"expr": "sum(container_memory_working_set_bytes{name!=\"\",name=~\"$container\"}) / scalar(sum(machine_memory_bytes)) * 100", "legendFormat": "Memoria", "refId": "A"}],
      "gridPos": {"x": 8, "y": 39, "w": 8, "h": 3},
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "textMode": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"]}
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percent",
          "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}
        }
      }
    },
    {
      "id": 22,
      "type": "stat",
      "title": "Red Total Contenedores (Ahora)",
      "description": "In + Out agregado de los contenedores seleccionados",
      "targets": [{"expr": "sum(rate(container_network_receive_bytes_total{name!=\"\",name=~\"$container\"}[$interval])) + sum(rate(container_network_transmit_bytes_total{name!=\"\",name=~\"$container\"}[$interval]))", "legendFormat": "Red", "refId": "A"}],
      "gridPos": {"x": 16, "y": 39, "w": 8, "h": 3},
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "textMode": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"]}
      },
      "fieldConfig": {
        "defaults": {
          "unit": "Bps",
          "color": {"mode": "fixed", "fixedColor": "purple"}
        }
      }
    },
    {
      "id": 10,
      "type": "timeseries",
      "title": "Uso de CPU por Contenedor",
      "targets": [{"expr": "sum(rate(container_cpu_usage_seconds_total{name!=\"\",name=~\"$container\"}[$interval])) by (name) * 100", "legendFormat": "{{name}}", "refId": "A"}],
      "gridPos": {"x": 0, "y": 42, "w": 8, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {"defaults": {"unit": "percent", "color": {"mode": "palette-classic"}, "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2}}}
    },
    {
      "id": 11,
      "type": "timeseries",
      "title": "Uso de Memoria por Contenedor",
      "targets": [{"expr": "sum(container_memory_working_set_bytes{name!=\"\",name=~\"$container\"}) by (name) / 1024 / 1024", "legendFormat": "{{name}}", "refId": "A"}],
      "gridPos": {"x": 8, "y": 42, "w": 8, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {"defaults": {"unit": "mbytes", "color": {"mode": "palette-classic"}, "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2}}}
    },
    {
      "id": 12,
      "type": "timeseries",
      "title": "I/O de Disco y Red por Contenedor",
      "targets": [
        {"expr": "sum(rate(container_fs_reads_bytes_total{name!=\"\",name=~\"$container\"}[$interval])) by (name)", "legendFormat": "Disk Read - {{name}}", "refId": "A"},
        {"expr": "sum(rate(container_fs_writes_bytes_total{name!=\"\",name=~\"$container\"}[$interval])) by (name)", "legendFormat": "Disk Write - {{name}}", "refId": "B"},
        {"expr": "sum(rate(container_network_receive_bytes_total{name!=\"\",name=~\"$container\"}[$interval])) by (name)", "legendFormat": "Net In - {{name}}", "refId": "C"},
        {"expr": "sum(rate(container_network_transmit_bytes_total{name!=\"\",name=~\"$container\"}[$interval])) by (name)", "legendFormat": "Net Out - {{name}}", "refId": "D"}
      ],
      "gridPos": {"x": 16, "y": 42, "w": 8, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {"defaults": {"unit": "Bps", "color": {"mode": "palette-classic"}, "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2}}}
    },
    {
      "id": 203,
      "type": "row",
      "title": "Infraestructura AWS (CloudWatch)",
      "collapsed": false,
      "gridPos": {"x": 0, "y": 50, "w": 24, "h": 1},
      "panels": []
    },
    {
      "id": 23,
      "type": "stat",
      "title": "EC2 CPU (Ahora)",
      "description": "CPU actual de la instancia EC2 seleccionada",
      "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
      "pluginVersion": "12.4.2",
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EC2",
          "metricName": "CPUUtilization",
          "dimensions": {"InstanceId": "$instance"},
          "statistic": "Average",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": ""
        }
      ],
      "gridPos": {"x": 0, "y": 51, "w": 12, "h": 3},
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "textMode": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"]}
      },
      "fieldConfig": {
        "defaults": {
          "unit": "percent",
          "color": {"mode": "thresholds"},
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 70}, {"color": "red", "value": 90}]}
        }
      }
    },
    {
      "id": 24,
      "type": "stat",
      "title": "EC2 Status Check (Ahora)",
      "description": "Chequeo combinado de salud de la instancia EC2 (hipervisor + SO) publicado por AWS. 0 = OK, >0 = falla. Respalda el SLO de disponibilidad desde el lado del proveedor.",
      "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
      "pluginVersion": "12.4.2",
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EC2",
          "metricName": "StatusCheckFailed",
          "dimensions": {"InstanceId": "$instance"},
          "statistic": "Maximum",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": ""
        }
      ],
      "gridPos": {"x": 12, "y": 51, "w": 12, "h": 3},
      "options": {
        "colorMode": "value",
        "graphMode": "area",
        "textMode": "auto",
        "reduceOptions": {"calcs": ["lastNotNull"]}
      },
      "fieldConfig": {
        "defaults": {
          "unit": "short",
          "color": {"mode": "thresholds"},
          "mappings": [
            {"options": {"0": {"color": "green", "text": "OK"}}, "type": "value"}
          ],
          "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "red", "value": 1}]}
        }
      }
    },
    {
      "id": 15,
      "type": "timeseries",
      "title": "EC2: Utilización de CPU (CloudWatch)",
      "description": "CPU Utilization from AWS CloudWatch",
      "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
      "pluginVersion": "12.4.2",
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EC2",
          "metricName": "CPUUtilization",
          "dimensions": {"InstanceId": "$instance"},
          "statistic": "Average",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": ""
        }
      ],
      "gridPos": {"x": 0, "y": 54, "w": 12, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {"defaults": {"unit": "percent", "color": {"mode": "palette-classic"}, "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2}}}
    },
    {
      "id": 16,
      "type": "timeseries",
      "title": "EC2: Red - Tráfico (CloudWatch)",
      "description": "Network In and Out from AWS CloudWatch",
      "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
      "pluginVersion": "12.4.2",
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EC2",
          "metricName": "NetworkIn",
          "dimensions": {"InstanceId": "$instance"},
          "statistic": "Average",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": "Network In"
        },
        {
          "refId": "B",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EC2",
          "metricName": "NetworkOut",
          "dimensions": {"InstanceId": "$instance"},
          "statistic": "Average",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": "Network Out"
        }
      ],
      "gridPos": {"x": 12, "y": 54, "w": 12, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {"defaults": {"unit": "bytes", "color": {"mode": "palette-classic"}, "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2}}}
    },
    {
      "id": 17,
      "type": "timeseries",
      "title": "EBS: Operaciones de I/O (CloudWatch)",
      "description": "EBS Volume Read and Write Ops from AWS CloudWatch",
      "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
      "pluginVersion": "12.4.2",
      "targets": [
        {
          "refId": "A",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EBS",
          "metricName": "VolumeReadOps",
          "dimensions": {},
          "statistic": "Average",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": "Read Ops"
        },
        {
          "refId": "B",
          "datasource": {"type": "cloudwatch", "uid": "aws-cloudwatch-final"},
          "queryMode": "Metrics",
          "metricQueryType": 0,
          "metricEditorMode": 0,
          "queryLanguage": "CWLI",
          "region": "default",
          "namespace": "AWS/EBS",
          "metricName": "VolumeWriteOps",
          "dimensions": {},
          "statistic": "Average",
          "matchExact": false,
          "period": "300",
          "expression": "",
          "sqlExpression": "",
          "logGroups": [],
          "id": "",
          "label": "Write Ops"
        }
      ],
      "gridPos": {"x": 0, "y": 62, "w": 24, "h": 8},
      "options": {"tooltip": {"mode": "multi"}, "legend": {"displayMode": "list", "placement": "bottom"}},
      "fieldConfig": {"defaults": {"unit": "ops", "color": {"mode": "palette-classic"}, "custom": {"drawStyle": "line", "fillOpacity": 15, "lineWidth": 2}}}
    }
  ]
}
