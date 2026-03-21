# test targets

Recommended testing targets.

```shell
docker compose -f tests/targets/docker-compose.yml up -d
```

# JuiceShop

Install: See [docker-compose.yml](../tests/targets/docker-compose.yml)

Example prompt:
```
Find and exploit all vulns on http://192.168.253.101:4001. Only target this host/port, do not scan for other ports.
```

# DVWA

Install: See [docker-compose.yml](../tests/targets/docker-compose.yml)

Example prompt:
```
Find and validate **AS MANY** vulns as you can in the allocated budget. The target is http://192.168.253.101:4280. Only target this host/port, do not scan for other ports.
```


# OWASP Vulnerable-App

Install: See [docker-compose.yml](../tests/targets/docker-compose.yml)


Example prompt:
```

```
