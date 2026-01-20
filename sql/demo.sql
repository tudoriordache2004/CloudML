-- 1) Toate atracțiile din Paris
SELECT attraction_name, category, city
FROM dbo.attractions
WHERE city = 'Paris'
ORDER BY category, attraction_name;

-- 2) Atracții deschise luni
SELECT attraction_name, open_time, close_time
FROM dbo.opening_hours
WHERE day_of_week = 'Monday' AND is_closed = 0
ORDER BY open_time;

-- 3) Atracții închise marți
SELECT attraction_name
FROM dbo.opening_hours
WHERE day_of_week = 'Tuesday' AND is_closed = 1
ORDER BY attraction_name;

-- 4) Top 5 cele mai scumpe bilete
SELECT TOP 5 attraction_name, ticket_type, price, currency
FROM dbo.tickets
ORDER BY price DESC;

-- 5) Cele mai ieftine opțiuni (<= 15 EUR)
SELECT attraction_name, ticket_type, price
FROM dbo.tickets
WHERE price <= 15
ORDER BY price, attraction_name;

-- 6) Cât costă “Adult” per atracție
SELECT attraction_name, price
FROM dbo.tickets
WHERE ticket_type LIKE 'Adult%'
ORDER BY price DESC;

-- 7) Program pentru o atracție (ex: Louvre)
SELECT day_of_week, open_time, close_time, is_closed
FROM dbo.opening_hours
WHERE attraction_name = 'Louvre Museum'
ORDER BY CASE day_of_week
  WHEN 'Monday' THEN 1 WHEN 'Tuesday' THEN 2 WHEN 'Wednesday' THEN 3
  WHEN 'Thursday' THEN 4 WHEN 'Friday' THEN 5 WHEN 'Saturday' THEN 6
  WHEN 'Sunday' THEN 7 ELSE 99 END;

-- 8) Atracții fără bilet (price = 0)
SELECT DISTINCT attraction_name
FROM dbo.tickets
WHERE price = 0
ORDER BY attraction_name;