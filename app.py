from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///vacation_system.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# 假期類別枚舉
class VacationType:
    SICK_LEAVE = 'sick_leave'  # 有薪病假
    UNPAID_LEAVE = 'unpaid_leave'  # 無薪請假
    ANNUAL_LEAVE = 'annual_leave'  # 年假

    @classmethod
    def get_chinese_name(cls, vacation_type):
        names = {
            cls.SICK_LEAVE: '有薪病假',
            cls.UNPAID_LEAVE: '無薪請假',
            cls.ANNUAL_LEAVE: '年假'
        }
        return names.get(vacation_type, vacation_type)

    @classmethod
    def get_all_types(cls):
        return [
            (cls.SICK_LEAVE, '有薪病假'),
            (cls.UNPAID_LEAVE, '無薪請假'),
            (cls.ANNUAL_LEAVE, '年假')
        ]

# 員工模型
class Employee(db.Model):
    __tablename__ = 'employees'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    employee_id = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    hire_date = db.Column(db.Date, nullable=False)
    department = db.Column(db.String(100), nullable=False)
    
    # 年假配額（每年）
    annual_leave_quota = db.Column(db.Integer, default=14)
    # 病假配額（每年）
    sick_leave_quota = db.Column(db.Integer, default=30)
    
    # 關聯假期申請
    vacation_requests = db.relationship('VacationRequest', backref='employee', lazy=True)
    
    def __repr__(self):
        return f'<Employee {self.name}>'
    
    def get_remaining_days(self, vacation_type, year=None):
        """計算指定假期類型的剩餘天數"""
        if year is None:
            year = datetime.now().year
        
        # 獲取該年度已使用的假期天數
        used_days = db.session.query(db.func.sum(VacationRequest.days)).filter(
            VacationRequest.employee_id == self.id,
            VacationRequest.vacation_type == vacation_type,
            VacationRequest.status == 'approved',
            db.extract('year', VacationRequest.start_date) == year
        ).scalar() or 0
        
        if vacation_type == VacationType.ANNUAL_LEAVE:
            return max(0, self.annual_leave_quota - used_days)
        elif vacation_type == VacationType.SICK_LEAVE:
            return max(0, self.sick_leave_quota - used_days)
        else:  # 無薪假期無限制
            return float('inf')

# 假期申請模型
class VacationRequest(db.Model):
    __tablename__ = 'vacation_requests'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    vacation_type = db.Column(db.String(50), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    days = db.Column(db.Integer, nullable=False)
    reason = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')  # pending, approved, rejected
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_by = db.Column(db.String(100))
    approved_at = db.Column(db.DateTime)
    
    def __repr__(self):
        return f'<VacationRequest {self.id}>'
    
    @property
    def vacation_type_chinese(self):
        return VacationType.get_chinese_name(self.vacation_type)
    
    @property
    def is_paid(self):
        """判斷是否為有薪假期"""
        return self.vacation_type in [VacationType.SICK_LEAVE, VacationType.ANNUAL_LEAVE]

# 路由定義
@app.route('/')
def index():
    """首頁 - 顯示所有員工"""
    employees = Employee.query.all()
    return render_template('index.html', employees=employees)

@app.route('/employee/<int:employee_id>')
def employee_detail(employee_id):
    """員工詳情頁面"""
    employee = Employee.query.get_or_404(employee_id)
    current_year = datetime.now().year
    
    # 計算各類假期剩餘天數
    remaining_annual = employee.get_remaining_days(VacationType.ANNUAL_LEAVE, current_year)
    remaining_sick = employee.get_remaining_days(VacationType.SICK_LEAVE, current_year)
    
    # 獲取該員工的假期申請
    vacation_requests = VacationRequest.query.filter_by(employee_id=employee_id).order_by(
        VacationRequest.created_at.desc()
    ).all()
    
    return render_template('employee_detail.html', 
                         employee=employee,
                         vacation_requests=vacation_requests,
                         remaining_annual=remaining_annual,
                         remaining_sick=remaining_sick,
                         current_year=current_year)

@app.route('/request_vacation/<int:employee_id>')
def request_vacation_form(employee_id):
    """假期申請表單頁面"""
    employee = Employee.query.get_or_404(employee_id)
    vacation_types = VacationType.get_all_types()
    return render_template('request_vacation.html', employee=employee, vacation_types=vacation_types)

@app.route('/submit_vacation_request', methods=['POST'])
def submit_vacation_request():
    """提交假期申請"""
    try:
        employee_id = request.form['employee_id']
        vacation_type = request.form['vacation_type']
        start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
        end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
        reason = request.form['reason']
        
        # 計算假期天數（包含開始和結束日期）
        days = (end_date - start_date).days + 1
        
        if days <= 0:
            flash('結束日期必須晚於或等於開始日期', 'error')
            return redirect(url_for('request_vacation_form', employee_id=employee_id))
        
        employee = Employee.query.get(employee_id)
        
        # 檢查假期配額
        if vacation_type in [VacationType.ANNUAL_LEAVE, VacationType.SICK_LEAVE]:
            remaining_days = employee.get_remaining_days(vacation_type, start_date.year)
            if days > remaining_days:
                flash(f'申請天數超過剩餘配額。剩餘天數：{remaining_days}', 'error')
                return redirect(url_for('request_vacation_form', employee_id=employee_id))
        
        # 創建假期申請
        vacation_request = VacationRequest(
            employee_id=employee_id,
            vacation_type=vacation_type,
            start_date=start_date,
            end_date=end_date,
            days=days,
            reason=reason
        )
        
        db.session.add(vacation_request)
        db.session.commit()
        
        flash('假期申請已成功提交', 'success')
        return redirect(url_for('employee_detail', employee_id=employee_id))
        
    except Exception as e:
        flash(f'提交失敗：{str(e)}', 'error')
        return redirect(url_for('request_vacation_form', employee_id=employee_id))

@app.route('/approve_vacation/<int:request_id>')
def approve_vacation(request_id):
    """批准假期申請"""
    vacation_request = VacationRequest.query.get_or_404(request_id)
    vacation_request.status = 'approved'
    vacation_request.approved_at = datetime.utcnow()
    vacation_request.approved_by = 'HR Manager'  # 在實際系統中應該是當前登錄用戶
    
    db.session.commit()
    flash('假期申請已批准', 'success')
    return redirect(url_for('employee_detail', employee_id=vacation_request.employee_id))

@app.route('/reject_vacation/<int:request_id>')
def reject_vacation(request_id):
    """拒絕假期申請"""
    vacation_request = VacationRequest.query.get_or_404(request_id)
    vacation_request.status = 'rejected'
    vacation_request.approved_at = datetime.utcnow()
    vacation_request.approved_by = 'HR Manager'
    
    db.session.commit()
    flash('假期申請已拒絕', 'warning')
    return redirect(url_for('employee_detail', employee_id=vacation_request.employee_id))

@app.route('/add_employee')
def add_employee_form():
    """添加員工表單"""
    return render_template('add_employee.html')

@app.route('/submit_employee', methods=['POST'])
def submit_employee():
    """提交新員工信息"""
    try:
        name = request.form['name']
        employee_id = request.form['employee_id']
        email = request.form['email']
        hire_date = datetime.strptime(request.form['hire_date'], '%Y-%m-%d').date()
        department = request.form['department']
        
        employee = Employee(
            name=name,
            employee_id=employee_id,
            email=email,
            hire_date=hire_date,
            department=department
        )
        
        db.session.add(employee)
        db.session.commit()
        
        flash('員工已成功添加', 'success')
        return redirect(url_for('index'))
        
    except Exception as e:
        flash(f'添加失敗：{str(e)}', 'error')
        return redirect(url_for('add_employee_form'))

@app.route('/vacation_report')
def vacation_report():
    """假期報告頁面"""
    current_year = datetime.now().year
    employees = Employee.query.all()
    
    report_data = []
    for employee in employees:
        annual_used = db.session.query(db.func.sum(VacationRequest.days)).filter(
            VacationRequest.employee_id == employee.id,
            VacationRequest.vacation_type == VacationType.ANNUAL_LEAVE,
            VacationRequest.status == 'approved',
            db.extract('year', VacationRequest.start_date) == current_year
        ).scalar() or 0
        
        sick_used = db.session.query(db.func.sum(VacationRequest.days)).filter(
            VacationRequest.employee_id == employee.id,
            VacationRequest.vacation_type == VacationType.SICK_LEAVE,
            VacationRequest.status == 'approved',
            db.extract('year', VacationRequest.start_date) == current_year
        ).scalar() or 0
        
        unpaid_used = db.session.query(db.func.sum(VacationRequest.days)).filter(
            VacationRequest.employee_id == employee.id,
            VacationRequest.vacation_type == VacationType.UNPAID_LEAVE,
            VacationRequest.status == 'approved',
            db.extract('year', VacationRequest.start_date) == current_year
        ).scalar() or 0
        
        report_data.append({
            'employee': employee,
            'annual_used': annual_used,
            'annual_remaining': employee.annual_leave_quota - annual_used,
            'sick_used': sick_used,
            'sick_remaining': employee.sick_leave_quota - sick_used,
            'unpaid_used': unpaid_used
        })
    
    return render_template('vacation_report.html', report_data=report_data, current_year=current_year)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # 創建示例數據（如果數據庫為空）
        if Employee.query.count() == 0:
            sample_employees = [
                Employee(name='張三', employee_id='EMP001', email='zhang.san@company.com', 
                        hire_date=date(2023, 1, 15), department='技術部'),
                Employee(name='李四', employee_id='EMP002', email='li.si@company.com', 
                        hire_date=date(2022, 6, 1), department='市場部'),
                Employee(name='王五', employee_id='EMP003', email='wang.wu@company.com', 
                        hire_date=date(2021, 3, 10), department='人事部')
            ]
            
            for emp in sample_employees:
                db.session.add(emp)
            
            db.session.commit()
            print("示例數據已創建")
    
    app.run(debug=True, host='0.0.0.0', port=5000)