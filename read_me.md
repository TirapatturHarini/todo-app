cd frontend
docker build -t harinitirapattur/todo-otel-app-frontend:v1 .
cd ../backend
docker build -t harinitirapattur/todo-otel-app-backend:v2  .
# :latest images are failing to toggle the button
docker push harinitirapattur/todo-otel-app-frontend:v1
docker push harinitirapattur/todo-otel-app-backend:v2

docker push harinitirapattur/todo-otel-app-frontend:v2
# all updated changes of application functionality

cd frontend
docker build -t harinitirapattur/todo-otel-app-frontend:v5 .
cd ../backend
docker build -t harinitirapattur/todo-otel-app-backend:v5  .
docker push harinitirapattur/todo-otel-app-backend:v5

docker push harinitirapattur/todo-otel-app-frontend:v5
# for insrumentation part
cd frontend
docker build -t harinitirapattur/todo-otel-app-frontend:v6 .
cd ../backend
docker build -t harinitirapattur/todo-otel-app-backend:v6  .
docker push harinitirapattur/todo-otel-app-backend:v6

docker push harinitirapattur/todo-otel-app-frontend:v6

# for making trace id as constant size and logs labels

cd frontend
docker build -t harinitirapattur/todo-otel-app-frontend:v7 .
cd ../backend
docker build -t harinitirapattur/todo-otel-app-backend:v7  .
docker push harinitirapattur/todo-otel-app-backend:v7
docker push harinitirapattur/todo-otel-app-frontend:v7

# for implementing target allocator
- trying to create dplicates metrics first
cd backend
docker build -t harinitirapattur/todo-otel-app-backend:v8  .
docker push harinitirapattur/todo-otel-app-backend:v8

# for enhanced traces and logs 

cd backend
docker build -t harinitirapattur/todo-otel-app-backend:v16  .
docker push harinitirapattur/todo-otel-app-backend:v16

for no cache 
docker build --no-cache -t harinitirapattur/todo-otel-app-backend:v16 .
docker push harinitirapattur/todo-otel-app-backend:v16

for all instrumenteed things 
docker build  --no-cache -t harinitirapattur/todo-otel-app-backend:v21 .
docker push harinitirapattur/todo-otel-app-backend:v21
docker build -t harinitirapattur/todo-otel-app-backend:v23 .
docker push harinitirapattur/todo-otel-app-backend:v23

# for exemplars and business labels using the instrumented things v:30

#demoed with 37 version of backend image