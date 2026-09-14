SELECT s.id,c.name FROM student AS s JOIN class c ON s.cid=c.id;
SELECT age,COUNT(*) AS n FROM student GROUP BY age;
CREATE TABLE metrics(value FLOAT,active BOOL);
INSERT INTO metrics VALUES(3.14,TRUE);
SELECT * FROM student WHERE score>10+2*4;
