## binglog 恢复方式
```
SET global SQL_MODE='TRADITIONAL,ALLOW_INVALID_DATES';
SET GLOBAL sql_mode = 'ALLOW_INVALID_DATES';
SET GLOBAL sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION';
前提：
在删除确实时间点 和结束点没有发生过DDL 
操作步骤
1. mysqlbinlog --no-defaults --base64-output=decode-rows --skip-gtids=true -v --start-datetime  "2021-11-17 16:31:48"   --stop-datetime "2021-11-17 16:51:48" --database kmcustomer  mysql-bin.003823 > test_binlog.sql
找到对应删除确实时间点 和结束点
2. mysqlbinlog  --skip-gtids=true --start-datetime  "xxx" --stop-datetime "xxx" --database kmcustomer  mysql-bin.003823  > exec_binlog.sql
3. 导出整个库的表结构 
mysqldump -u xxx -h xxx -p"xxx" --quick --lock-tables=false --skip-add-locks --skip-triggers --set-gtid-purged=OFF --default-character-set=utf8 --no-data --databases kmcustomer > schema.sql
4. 在本地安装的mysql库执行以下语句 
mysql -h 127.0.0.1 -proot < schema.sql
mysql -h 127.0.0.1 -proot < exec_binlog.sql
5. 找回数据 
```
## delete 恢复方式
```
cat test_binlog.sql | awk '/DELETE FROM/ && (/kmcustomer.km_tbl_customer/ || /`kmcustomer`.`km_tbl_customer`/){
    while(1){
        print $0;
        getline;
        if($0 !~ /^###/){
            break;
        };
    }
}' > test.yq.delete.txt

sed -i 's/^### //g' test.yq.delete.txt
sed -i "s/^DELETE FROM/INSERT INTO/g" test.yq.delete.txt
sed -i "s/^WHERE/VALUES(/g" test.yq.delete.txt
sed -i '/@35=.*/a );' test.yq.delete.txt
cat test.yq.delete.txt  | awk -F"=|/*" '{
    if($0 ~ /^INSERT|^VALUES|^);/){
        print $0;
    }else{
        printf $2",";
    };
}' > test.yq.insert.sql
## 再if嵌套
cat test.yq.delete.txt  | awk -F"=|/*" '{
    if($0 ~ /^INSERT|^VALUES|^);/){
        print $0;
    }else{
	    if($1 ~ /@8/){
	        printf "FROM_UNIXTIME("$2"),";
	    }else if($1 ~ /@35/){
	        printf $2;
	    }else{ 
	        printf $2",";
	    }
    };
}' > test.yq.insert.sql11
```